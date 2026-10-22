"""`chvideo auto`: find music videos for the songs in a library that don't have one, and sync them.

Each song without a video is searched on YouTube.  The results are ranked by how well their title
matches the song and how much they look like the official music video (lyric videos, live
versions, covers, ... are left out).  The best one is then synced like a pinned `chvideo batch`
line: it's only installed if the song's audio is found in it, and otherwise the next-best result
is tried.

Songs that no video could be found for can be blocked: they're listed in chvideo-blocked.txt in
the library folder, and skipped by later runs.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path

from . import align as A
from .batch import Job, _short, add_batch_options, execute
from .cookies import CookieError
from .download import DownloadError, RateLimited, VideoMeta, resolve_source
from .library import (
    LibrarySong, chart_version, judge_video, normalize, rank_videos, scan_library, strip_annotations, trusted_uploader,
)
from .media import MediaError, require_tools
from .missing import missing_videos
from .osutil import read_text_any
from .pipeline import add_render_options, load_cookies
from .songdir import SongError

SEARCH_RESULTS = 10
MAX_CHECKS = 5  # search results per song that are judged
BLOCKLIST = "chvideo-blocked.txt"
BLOCKLIST_HEADER = [
    "# Songs `chvideo auto` doesn't look for videos for: one song folder per line, relative to this folder.",
    "# Delete a line to let it try that song again.",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="chvideo auto",
        description="Find and sync music videos for every song in your library that doesn't have one. "
        "Each song is searched on YouTube, the result that looks most like the official music video is "
        "picked, and it's only installed if the song's audio is found in it (otherwise the next result "
        "is tried). Shows the plan and asks before starting.",
    )
    p.add_argument("library", nargs="?", type=Path, default=os.environ.get("CHVIDEO_LIBRARY"),
                   help="Clone Hero songs folder (default: $CHVIDEO_LIBRARY)")
    p.add_argument("--only", metavar="TEXT",
                   help="Only songs whose 'Artist - Song' contains TEXT (case-insensitive)")
    p.add_argument("--limit", type=int, metavar="N", help="Only the first N songs without a video (alphabetically)")
    p.add_argument("--tries", type=int, default=3, metavar="N",
                   help="Search results to try per song before giving up (default 3)")
    p.add_argument("--min-score", type=float, default=80.0,
                   help="Minimum title-match score (0-100) for a search result to be considered (default 80)")
    p.add_argument("--allow-unofficial", action="store_true",
                   help="Also consider uploads that don't look official (fan re-uploads of old videos, ...); "
                   "lyric videos, covers and playthroughs are still left out")
    p.add_argument("--block-failed", action="store_true",
                   help=f"Add songs no video could be found for to {BLOCKLIST} without asking")
    p.add_argument("--retry-blocked", action="store_true", help=f"Also try the songs listed in {BLOCKLIST}")
    add_batch_options(p)
    add_render_options(p)
    return p.parse_args(argv)


def search_query(song: LibrarySong) -> str:
    # Chart annotations ("(WaveGroup)", "(having a totally normal one)") only confuse the search,
    # but a "(Live)" chart should find live videos; "official music video" would pull it towards
    # the studio video instead.
    version = " ".join(sorted(chart_version(song))) or "official music video"
    return f"{strip_annotations(song.artist)} {strip_annotations(song.name)} {version}".strip()


def read_blocklist(root: Path) -> set[Path]:
    path = root / BLOCKLIST
    if not path.exists():
        return set()
    lines = (line.strip() for line in read_text_any(path).splitlines())
    return {(root / line).resolve() for line in lines if line and not line.startswith("#")}


def add_to_blocklist(root: Path, jobs: list[Job]) -> Path:
    path = root / BLOCKLIST
    existing = read_text_any(path) if path.exists() else ""
    lines = [] if existing else list(BLOCKLIST_HEADER)
    for job in jobs:
        lines.append(f"# {date.today()}: {job.detail}")
        lines.extend(s.path.relative_to(root).as_posix() for s in job.explicit)
    with path.open("a", encoding="utf-8") as f:
        f.write(("\n" if existing and not existing.endswith("\n") else "") + "\n".join(lines) + "\n")
    return path


def find_videos(song: LibrarySong, args: argparse.Namespace) -> tuple[list[VideoMeta], str, bool]:
    """Search for the song's music video.  A likely result from the artist's or a label's channel is
    trusted as it is; any other is only used if its full metadata (whole description, ...) shows it's
    legitimate, e.g. a director's upload that credits its crew.  Fetching that costs a request, so
    it's only done where needed.  -> (videos to try, why none were found, whether that's final)"""
    bare = replace(song, artist=strip_annotations(song.artist))  # "Black Sabbath (WaveGroup)"
    results = resolve_source(f"ytsearch{SEARCH_RESULTS}:{search_query(song)}")
    picks: list[VideoMeta] = []
    reasons: Counter[str] = Counter()
    unchecked = 0
    for meta in rank_videos(bare, results, args.min_score)[:MAX_CHECKS]:
        if not (args.allow_unofficial or trusted_uploader(bare, meta)):
            try:
                meta = resolve_source(meta.source)[0]
            except RateLimited:
                raise
            except DownloadError:
                unchecked += 1  # age-restricted without cookies, ...: judge what the search showed
        if reason := judge_video(bare, meta, unofficial=args.allow_unofficial):
            reasons[reason] += 1
            continue
        picks.append(meta)
        if len(picks) >= max(1, args.tries):
            break
    if picks:
        return picks, "", False
    if not reasons:
        return [], "no music video found in the search results", True
    why = ", ".join(f"{r} x{n}" if n > 1 else r for r, n in reasons.most_common())
    # if some results couldn't be checked properly, a later run (or --cookies) might do better
    return [], f"no music video found (rejected: {why})", unchecked == 0


def _plan(args: argparse.Namespace, root: Path) -> list[Job]:
    t = time.time()
    library = scan_library(root)
    groups = missing_videos(library)
    blocked = set() if args.retry_blocked else read_blocklist(root)
    unblocked = [[s for s in g if s.path not in blocked] for g in groups]
    unblocked = [g for g in unblocked if g]
    note = (f", {len(groups) - len(unblocked)} of them blocked in {BLOCKLIST} (--retry-blocked to include them)"
            if len(unblocked) < len(groups) else "")
    print(f"Library: {len(library)} songs, {len(groups)} without a video{note}, scanned in {time.time() - t:.1f}s")
    groups = unblocked
    if args.only:
        groups = [g for g in groups if normalize(args.only) in normalize(g[0].title)]
    if args.limit is not None:
        groups = groups[: args.limit]
    if not groups:
        return []

    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(find_videos, g[0], args) for g in groups]
        for done, (group, fut) in enumerate(zip(groups, futures, strict=True), 1):
            song = group[0]
            no_pick = VideoMeta(f"ytsearch1:{search_query(song)}", song.title)
            try:
                picks, why, final = fut.result()
            except RateLimited:
                # every further request would fail too; keep what was found and leave the rest
                for f in futures:
                    f.cancel()
                sys.stdout.write("\n")
                print(f"  YouTube started rate-limiting after {done - 1} of {len(groups)} songs, so the rest "
                      "are left for a later run. Wait a while (often an hour), or pass --cookies (see cookies.md).")
                break
            except Exception as e:  # noqa: BLE001 - report and carry on
                jobs.append(Job(no_pick, explicit=group, status="fail", detail=f"search failed: {_short(e)}"))
            else:
                if picks:
                    jobs.append(Job(picks[0], explicit=group, alternatives=picks[1:]))
                else:
                    jobs.append(Job(no_pick, explicit=group, status="fail", permanent=final, detail=why))
            sys.stdout.write(f"\r  searching YouTube and checking videos {done}/{len(groups)}")
            sys.stdout.flush()
    sys.stdout.write("\n")
    return jobs


def _offer_block(jobs: list[Job], root: Path, args: argparse.Namespace) -> None:
    """Offer to block the songs that failed for good (not for download errors, rate limits, ...)."""
    dead = [j for j in jobs if j.status == "fail" and j.permanent]
    if not dead or any(j.status == "pending" for j in jobs):  # --plan, declined, or interrupted
        return
    print(f"\nNo matching video was found for {len(dead)} song(s):")
    for j in dead:
        print(f"  {j.explicit[0].title}")
    if not args.block_failed:
        if args.yes or not sys.stdin.isatty():
            print("Run with --block-failed to block songs like these so later runs skip them.")
            return
        answer = input("Block them so `chvideo auto` skips them next time? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            return
    path = add_to_blocklist(root, dead)
    print(f"Blocked {len(dead)} song(s) in {path} (delete a line there to unblock)")


def run(args: argparse.Namespace) -> int:
    if args.library is None:
        raise SongError("Pass a songs folder (or set CHVIDEO_LIBRARY).")
    require_tools()
    load_cookies(args)
    root = Path(args.library).expanduser().resolve()
    jobs = _plan(args, root)
    if not jobs:
        print("No songs to find videos for.")
        return 0
    code = execute(jobs, args)
    if not args.plan:
        _offer_block(jobs, root, args)
    return code


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
