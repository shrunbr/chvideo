"""The per-song steps shared by single and batch mode: align, render, install."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import align as A
from . import cookies
from .media import MediaError, VideoInfo, mix_audio
from .osutil import cache_dir, is_local_file
from .render import EncodeSettings, build_timeline, make_preview, nvenc_status, render
from .songdir import SongFolder, read_ini_values, set_ini_value, write_minimal_ini


def fmt_t(t: float) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:06.3f}"


def add_render_options(p: argparse.ArgumentParser) -> None:
    """Options shared by `chvideo` and `chvideo batch`."""
    p.add_argument("--nudge-ms", type=int, default=0,
                   help="Written to song.ini video_start_time for fine tuning in-game "
                   "(positive = video ahead, negative = video behind). Default 0")
    p.add_argument("--max-height", type=int, default=1080, help="Max output height (default 1080)")
    p.add_argument("--fps", type=str, default=None, help="Output frame rate (default: source, capped at 60)")
    p.add_argument("--format", choices=("webm", "mp4"), default=None,
                   help="webm = VP8 (default on CPU, works on every platform); mp4 = H.264 (Windows/Mac only; "
                   "always used with --gpu)")
    p.add_argument("--gpu", action="store_true",
                   help="Encode on an NVIDIA GPU (NVENC, H.264 .mp4). Falls back to the CPU if it isn't available")
    p.add_argument("--quality", type=int, default=10, help="Encoder CRF, lower = better/larger (default 10)")
    p.add_argument("--jobs", type=int, default=None,
                   help="Parallel encoder processes (default: one per physical CPU core)")
    p.add_argument("--gap-fill", choices=("continue", "black"), default="continue",
                   help="What to show where the song has audio the video doesn't: keep the "
                   "neighbouring footage playing (default) or black")
    p.add_argument("--min-coverage", type=float, default=0.5,
                   help="Abort if less than this fraction of the song is found in the video (default 0.5)")
    p.add_argument("--dry-run", action="store_true", help="Analyse and render, but don't touch the song folder")
    p.add_argument("--force", action="store_true", help="Replace an existing video (old one is kept as *.bak)")
    p.add_argument("--refresh", action="store_true", help="Re-download even if the video is cached")
    p.add_argument("--keep-downloads", action="store_true",
                   help="Keep downloaded videos in the cache after a successful install")
    p.add_argument("--cookies", type=Path, default=os.environ.get("CHVIDEO_COOKIES"),
                   help="Netscape-format cookies.txt for age-restricted videos (default: $CHVIDEO_COOKIES). "
                   "The file is only read, never modified")


def load_cookies(args: argparse.Namespace, log=print) -> None:
    """Validate and activate --cookies (raises CookieError on a bad file)."""
    if not args.cookies:
        cookies.set_active(None)
        return
    cf = cookies.load_cookie_file(Path(args.cookies))
    cookies.set_active(cf)
    log(f"Cookies: {cf.youtube_cookies} youtube.com cookies from {cf.path}")
    for w in cf.warnings:
        log(f"  warning: {w}")


@dataclass
class SyncOptions:
    nudge_ms: int = 0
    min_coverage: float = 0.5
    gap_fill: str = "continue"
    dry_run: bool = False
    offset_ms: float | None = None
    preview: str | None = None  # None = no preview, "" = default location
    encode: EncodeSettings | None = None

    @classmethod
    def from_args(cls, args: argparse.Namespace, log=print) -> SyncOptions:
        encoder, container = "cpu", args.format or "webm"
        if args.gpu:
            if args.format == "webm":
                raise MediaError("GPU encoders can't produce VP8 .webm; drop --format webm to use --gpu (H.264 .mp4).")
            if (problem := nvenc_status()) is None:
                encoder, container = "nvenc", "mp4"
                log("Encoder: NVIDIA GPU (NVENC H.264 -> video.mp4)")
            else:
                log(f"Encoder: GPU not available ({problem}); falling back to CPU ({container})")
        return cls(
            nudge_ms=args.nudge_ms,
            min_coverage=args.min_coverage,
            gap_fill=args.gap_fill,
            dry_run=args.dry_run,
            offset_ms=getattr(args, "offset_ms", None),
            preview=getattr(args, "preview", None),
            encode=EncodeSettings(
                container=container,
                max_height=args.max_height,
                fps=Fraction(args.fps) if args.fps else None,
                quality=args.quality,
                jobs=args.jobs,
                encoder=encoder,
            ),
        )


def align_song(
    song: SongFolder, info: VideoInfo, vid_audio: np.ndarray | None, opts: SyncOptions, log=print
) -> A.AlignResult:
    """Align and print the report.  Raises AlignError if too little of the song is in the video."""
    song_audio = mix_audio(song.stems, A.SR)
    song_dur = len(song_audio) / A.SR
    if opts.offset_ms is not None:
        result = A.manual_alignment(song_dur, info.duration, opts.offset_ms / 1000)
    else:
        if vid_audio is None:
            raise MediaError("The video has no audio track to align with; pass --offset-ms instead.")
        result = A.align(song_audio, vid_audio, log=log)
    print_report(result, log)
    if opts.offset_ms is None and result.coverage < opts.min_coverage:
        raise A.AlignError(
            f"Only {result.coverage:.0%} of the song was found in the video (minimum {opts.min_coverage:.0%}). "
            "This is probably a different recording (live version, remix, re-record) or the wrong video. "
            "Use --min-coverage to accept it anyway, or --offset-ms to set the offset by hand."
        )
    return result


def render_and_install(
    song: SongFolder, info: VideoInfo, result: A.AlignResult, opts: SyncOptions, log=print
) -> Path:
    """Render the synced video; install it unless dry-run.  Returns the final video path."""
    enc = opts.encode or EncodeSettings()
    pieces = build_timeline(result, info.duration, opts.gap_fill)
    for pc in pieces:
        what = "black" if pc.v0 is None else f"video {fmt_t(pc.v0)}" + (f" x{pc.speed:.5f}" if pc.speed != 1 else "")
        log(f"  song {fmt_t(pc.t0)} - {fmt_t(pc.t1)}  <-  {what}  ({pc.kind})")
    work = cache_dir() / "work" / re.sub(r"[^\w.-]+", "_", song.path.name)[:80]
    work.mkdir(parents=True, exist_ok=True)
    rendered = work / f"video.{enc.container}"
    try:
        render(pieces, info, rendered, enc, result.song_duration)
    except MediaError as e:
        if enc.encoder != "nvenc":
            raise
        # e.g. the GPU's encoder sessions are busy (game recording / streaming software)
        log(f"  GPU encode failed ({str(e).splitlines()[-1][:160]}); re-encoding on the CPU")
        enc = replace(enc, encoder="cpu", container="webm")
        rendered = work / f"video.{enc.container}"
        render(pieces, info, rendered, enc, result.song_duration)

    if opts.preview is not None:
        preview = Path(opts.preview) if opts.preview else work / "preview.webm"
        if enc.container == "mp4" and preview.suffix.lower() == ".webm":
            preview = preview.with_suffix(".mkv")
        make_preview(rendered, song.stems, preview)
        log(f"  preview with chart audio: {preview}")

    if opts.dry_run:
        log(f"  dry run - rendered video left at {rendered}")
        return rendered
    target = install(song, rendered, enc.container, opts.nudge_ms, song.existing_videos(), log)
    rendered.unlink(missing_ok=True)
    return target


def print_report(result: A.AlignResult, log=print) -> None:
    log(f"  Song length {fmt_t(result.song_duration)}, video soundtrack {fmt_t(result.video_duration)}, "
        f"{result.coverage:.0%} of the song found in the video")
    for s in result.segments:
        precision = (f"±{max(s.fine_spread_ms, 0.05):.1f} ms ({s.fine_chunks} checks)" if s.fine_chunks
                     else "~10 ms (coarse)")
        drift = f", speed x{s.speed:.5f}" if s.speed != 1.0 else ""
        log(f"  song {fmt_t(s.song_start)} - {fmt_t(s.song_end)}: video offset {s.offset * 1000:+.1f} ms"
            f"{drift}  match {s.confidence:.2f}  precision {precision}")
    if len(result.segments) == 1 and result.segments[0].speed == 1.0:
        log(f"  (equivalent to video_start_time = {round(result.segments[0].offset * 1000)} on the original video)")
    for n in result.notes:
        log(f"  note: {n}")


def install(song: SongFolder, rendered: Path, ext: str, nudge_ms: int, existing: list[Path], log=print) -> Path:
    for old in existing:
        bak = old.with_name(old.name + ".bak")
        i = 1
        while bak.exists():
            bak = old.with_name(f"{old.name}.{i}.bak")
            i += 1
        old.rename(bak)
        log(f"  moved existing {old.name} -> {bak.name}")
    target = song.path / f"video.{ext}"
    shutil.copyfile(rendered, target)
    log(f"  wrote {target}")
    if song.ini is not None:
        old_val = read_ini_values(song.ini).get("video_start_time")
        set_ini_value(song.ini, "video_start_time", str(nudge_ms))
        changed = f" (was {old_val})" if old_val not in (None, str(nudge_ms)) else ""
        log(f"  song.ini: video_start_time = {nudge_ms}{changed}")
    elif nudge_ms:
        write_minimal_ini(song.path / "song.ini", "video_start_time", str(nudge_ms))
        log(f"  created song.ini with video_start_time = {nudge_ms}")
    return target


def discard_download(video_path: Path, source: str) -> None:
    """Remove a cached download (never a user's own local file)."""
    if is_local_file(source):
        return
    # only ever a single downloads/<key>/ folder created by fetch_video
    if video_path.parent.parent == cache_dir() / "downloads":
        shutil.rmtree(video_path.parent, ignore_errors=True)
