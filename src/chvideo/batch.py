"""`chvideo batch`: match many music videos to songs in a library and sync them all.

Each video is matched to a song by title/channel (fuzzy), then the match is *confirmed by
audio*: the video is only installed if the chart's audio is actually found in it.  If the
best title match fails that check, the next-best candidate is tried.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from . import align as A
from .cookies import CookieError
from .download import DownloadError, RateLimited, VideoMeta, fetch_video, resolve_source
from .library import Candidate, LibrarySong, candidates, resolve_folder, scan_library
from .media import VIDEO_CONTAINER_EXTS, MediaError, decode_audio, require_tools, video_info
from .osutil import cache_dir, is_local_file, read_text_any
from .pipeline import (
    SyncOptions, add_render_options, align_song, discard_download, fmt_t, load_cookies, render_and_install,
)
from .songdir import SongError, load_song_folder


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="chvideo batch",
        description="Sync many music videos at once. Give video URLs, YouTube playlist URLs, local video "
        "files, or text files listing them (one per line; '#' comments allowed). Each video is matched "
        "to a song in your library by its title, and the match is confirmed by comparing audio. To pin "
        "a video to a specific song, write the line as:  URL | song folder",
    )
    p.add_argument("inputs", nargs="+", help="URLs, playlist URLs, video files, or text files of links")
    p.add_argument("--library", type=Path, default=os.environ.get("CHVIDEO_LIBRARY"),
                   help="Clone Hero songs folder to match against (default: $CHVIDEO_LIBRARY)")
    p.add_argument("--min-score", type=float, default=80.0,
                   help="Minimum title-match score (0-100) for a song to be considered (default 80)")
    add_batch_options(p)
    add_render_options(p)
    return p.parse_args(argv)


def add_batch_options(p: argparse.ArgumentParser) -> None:
    """Options shared by `chvideo batch` and `chvideo auto`."""
    p.add_argument("--plan", action="store_true", help="Only show which video goes to which song")
    p.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    p.add_argument("--preview", action="store_true",
                   help="Also write a preview (video + chart audio) for each song into the cache")
    p.add_argument("--parallel-downloads", type=int, default=2,
                   help="How many videos to download ahead while encoding (default 2)")


@dataclass
class Job:
    meta: VideoMeta
    explicit: list[LibrarySong] | None = None  # from "URL | folder"
    tries: list[Candidate] = field(default_factory=list)
    alternatives: list[VideoMeta] = field(default_factory=list)  # other search results, tried in order
    status: str = "pending"  # pending | ok | skip | fail
    detail: str = ""
    permanent: bool = False  # failed for a reason that retrying won't fix (no video matched the audio)
    installed: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- input parsing

def _read_inputs(items: list[str]) -> list[tuple[str, str | None]]:
    """-> [(source, explicit_folder_or_None)]"""
    out: list[tuple[str, str | None]] = []
    for item in items:
        path = Path(item).expanduser()
        if is_local_file(item) and path.suffix.lower() not in VIDEO_CONTAINER_EXTS:
            for raw in read_text_any(path).splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                src, sep, folder = (line.partition("\t") if "\t" in line else line.partition(" | "))
                out.append((src.strip(), folder.strip() or None if sep else None))
        else:
            out.append((item, None))
    return out


def _resolve_all(entries: list[tuple[str, str | None]]) -> tuple[list[tuple[VideoMeta, str | None]], list[Job]]:
    resolved: list[tuple[VideoMeta, str | None]] = []
    failed: list[Job] = []
    done = 0

    def work(entry: tuple[str, str | None]):
        return entry, resolve_source(entry[0])

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(work, e) for e in entries]
        for fut, entry in zip(futures, entries, strict=True):
            try:
                (src, folder), metas = fut.result()
                if folder and len(metas) > 1:
                    metas = metas[:1]
                resolved.extend((m, folder) for m in metas)
            except Exception as e:  # noqa: BLE001 - report and carry on
                failed.append(Job(VideoMeta(entry[0], entry[0]), status="fail",
                                  detail=f"could not read video info: {_short(e)}"))
            done += 1
            sys.stdout.write(f"\r  reading video info {done}/{len(entries)}")
            sys.stdout.flush()
    sys.stdout.write("\n")
    # de-duplicate (the same video listed twice, or in two playlists); a pinned
    # "URL | folder" line wins over an unpinned one, and different pins are all kept
    unique: dict[tuple[str, str | None], tuple[VideoMeta, str | None]] = {}
    for meta, folder in resolved:
        if folder is None and any(src == meta.source for src, _ in unique):
            continue
        if folder is not None:
            unique.pop((meta.source, None), None)
        unique.setdefault((meta.source, folder), (meta, folder))
    return list(unique.values()), failed


def _short(e: BaseException) -> str:
    msg = str(e).strip().splitlines()
    return (msg[-1] if msg else type(e).__name__)[:400]


# --------------------------------------------------------------------------- planning

def _plan(args: argparse.Namespace) -> list[Job]:
    entries = _read_inputs(args.inputs)
    if not entries:
        raise SongError("No videos given.")
    print(f"Reading {len(entries)} input(s)")
    resolved, jobs = _resolve_all(entries)

    library: list[LibrarySong] = []
    needs_library = any(folder is None for _, folder in resolved)
    if args.library:
        t = time.time()
        library = scan_library(Path(args.library))
        print(f"  library: {len(library)} songs ({sum(s.has_video for s in library)} already have a video), "
              f"scanned in {time.time() - t:.1f}s")
    elif needs_library:
        raise SongError("Pass --library <songs folder> (or set CHVIDEO_LIBRARY) so videos can be matched to songs, "
                        "or pin each video with 'URL | song folder' lines.")

    for meta, folder in resolved:
        job = Job(meta)
        if folder:
            try:
                job.explicit = resolve_folder(folder, Path(args.library) if args.library else None, library)
            except FileNotFoundError as e:
                job.status, job.detail = "fail", str(e)
            jobs.append(job)
            continue
        cands = [c for c in candidates(meta, library) if c.score >= args.min_score]
        if not cands:
            job.status, job.detail = "fail", "no matching song in the library"
        elif not args.force and all(s.has_video for s in cands[0].songs):
            job.status, job.detail = "skip", f"{cands[0].title} already has a video (use --force)"
        else:
            job.tries = [c for c in cands if args.force or any(not s.has_video for s in c.songs)]
        jobs.append(job)
    return jobs


def _print_plan(jobs: list[Job]) -> None:
    print("\nPlan:")
    width = min(60, max((len(j.meta.label) for j in jobs), default=10))
    for i, j in enumerate(jobs, 1):
        label = j.meta.label if len(j.meta.label) <= width else j.meta.label[: width - 1] + "…"
        if j.explicit and j.status == "pending":
            target = ", ".join(s.path.name for s in j.explicit)
            if j.alternatives:
                target += f"  (+{len(j.alternatives)} fallback video{'s' if len(j.alternatives) > 1 else ''})"
        elif j.tries:
            first = j.tries[0]
            charts = f" ({len(first.songs)} charts)" if len(first.songs) > 1 else ""
            target = f"{first.title}{charts}  [match {first.score:.0f}]"
            if len(j.tries) > 1:
                target += "  (fallback: " + ", ".join(c.title for c in j.tries[1:]) + ")"
        else:
            kind = ("SKIP" if j.status == "skip" else "NO MATCH" if j.detail.startswith("no matching")
                    else "FAILED")
            target = f"{kind}: {j.detail}"
        print(f"  {i:3d}. {label:<{width}}  ->  {target}")


# --------------------------------------------------------------------------- execution

def _sync_job(job: Job, video_path: Path, opts: SyncOptions, args: argparse.Namespace) -> None:
    info = video_info(video_path)
    print(f"  video: {info.width}x{info.height} @ {info.fps:.3f} fps, {fmt_t(info.duration)}")
    if not info.has_audio:
        raise MediaError("the video has no audio track, so it can't be matched or synced")
    vid_audio = decode_audio(video_path, A.SR)

    groups = [job.explicit] if job.explicit else [c.songs for c in job.tries]
    reasons = []
    for songs in groups:
        for lib_song in songs:
            if lib_song.has_video and not args.force:
                continue
            song = load_song_folder(lib_song.path)
            print(f"\n  -> {song.path.name}")
            try:
                result = align_song(song, info, vid_audio, opts, log=_indent)
            except A.AlignError as e:
                reasons.append(f"{song.path.name}: {e.args[0].split('.')[0]}")
                print(f"     not a match: {e.args[0].split('.')[0]}")
                continue
            render_and_install(song, info, result, opts, log=_indent)
            lib_song.has_video = True
            offsets = ", ".join(f"{s.offset * 1000:+.0f} ms" for s in result.segments)
            job.installed.append(f"{song.path.name}  (offset {offsets})")
        if job.installed:
            return
    raise A.AlignError("; ".join(reasons) or "no song left to sync (all already have videos)")


def _indent(msg: str = "") -> None:
    print("   " + msg)


def _sync_with_alternatives(job: Job, get_video, opts: SyncOptions, args: argparse.Namespace, cache: Path) -> Path:
    """Sync the job's video; if it can't be used and there are other search results, try those in turn."""
    while True:
        video_path = None
        try:
            video_path = get_video()
            _sync_job(job, video_path, opts, args)
            return video_path
        except RateLimited:
            raise  # the next video would fail the same way
        except (A.AlignError, MediaError, DownloadError) as e:
            if not job.alternatives:
                raise
            print(f"  not used: {_short(e)}")
            if video_path is not None:
                discard_download(video_path, job.meta.source)  # a rejected search result is no use later
            job.meta = job.alternatives.pop(0)
            print(f"\n  trying the next search result: {job.meta.label}\n  {job.meta.source}\n  downloading...")
            get_video = partial(fetch_video, job.meta.source, cache, args.max_height, refresh=args.refresh, quiet=True)


def _wait_for_download(fut: Future) -> Path:
    try:
        return fut.result(timeout=2)
    except TimeoutError:
        print("  downloading...")
        return fut.result()


def run(args: argparse.Namespace) -> int:
    require_tools()
    load_cookies(args)
    return execute(_plan(args), args)


def execute(jobs: list[Job], args: argparse.Namespace) -> int:
    """Show the plan, confirm, then download and sync every pending job."""
    _print_plan(jobs)
    todo = [j for j in jobs if j.status == "pending"]
    if args.plan or not todo:
        if not todo:
            print("\nNothing to do.")
        return 0
    if not args.yes and sys.stdin.isatty():
        answer = input(f"\nSync {len(todo)} video(s)? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            return 0

    opts = SyncOptions.from_args(args)
    opts.preview = "" if args.preview else None
    cache = cache_dir()
    t_start = time.time()
    lookahead = max(1, args.parallel_downloads)
    pool = ThreadPoolExecutor(max_workers=lookahead)
    downloads: dict[int, Future] = {}

    def ensure_downloads(upto: int) -> None:
        for k in range(min(upto, len(todo))):
            if k not in downloads:
                downloads[k] = pool.submit(fetch_video, todo[k].meta.source, cache, args.max_height,
                                           refresh=args.refresh, quiet=True)

    try:
        for i, job in enumerate(todo):
            ensure_downloads(i + 1 + lookahead)  # keep the next few downloading while we encode
            print(f"\n[{i + 1}/{len(todo)}] {job.meta.label}")
            print(f"  {job.meta.source}")
            try:
                video_path = _sync_with_alternatives(job, partial(_wait_for_download, downloads[i]), opts, args, cache)
                job.status = "ok"
                if not args.dry_run and not args.keep_downloads:
                    discard_download(video_path, job.meta.source)
            except KeyboardInterrupt:
                raise
            except RateLimited as e:
                job.status, job.detail = "fail", _short(e)
                print(f"  FAILED: {job.detail}\n\nStopping: every further download would fail the same way.")
                break
            except Exception as e:  # noqa: BLE001 - one bad video shouldn't stop the batch
                job.status, job.detail = "fail", _short(e)
                job.permanent = isinstance(e, A.AlignError)
                print(f"  FAILED: {job.detail}")
            downloads.pop(i, None)
    except KeyboardInterrupt:
        print("\ninterrupted - stopping after cleanup", file=sys.stderr)
        pool.shutdown(wait=False, cancel_futures=True)
        _summary(jobs, time.time() - t_start)
        return 130
    pool.shutdown(wait=True, cancel_futures=True)  # downloads queued before a rate-limit stop
    _summary(jobs, time.time() - t_start)
    return 0 if all(j.status != "fail" for j in jobs) else 1


def _summary(jobs: list[Job], elapsed: float) -> None:
    ok = [j for j in jobs if j.status == "ok"]
    skipped = [j for j in jobs if j.status == "skip"]
    failed = [j for j in jobs if j.status == "fail"]
    pending = [j for j in jobs if j.status == "pending"]
    print(f"\n{'=' * 70}\nSummary: {len(ok)} synced, {len(skipped)} skipped, {len(failed)} failed"
          + (f", {len(pending)} not started" if pending else "") + f"  ({elapsed / 60:.1f} min)")
    for j in ok:
        for target in j.installed:
            print(f"  OK    {target}")
    for j in skipped:
        print(f"  SKIP  {j.meta.label}: {j.detail}")
    for j in failed:
        print(f"  FAIL  {j.meta.label}: {j.detail}")
    if failed or pending:
        print("\nTo retry, put these in a file, add ' | <song folder>' where the match was wrong, and run it again:")
        for j in failed + pending:
            for folder in [s.path for s in j.explicit] if j.explicit else [None]:
                print(f"  {j.meta.source}" + (f" | {folder}" if folder else ""))


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (SongError, MediaError, A.AlignError, FileNotFoundError, CookieError, DownloadError) as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
