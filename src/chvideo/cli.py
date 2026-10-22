"""chvideo: download a music video and sync it to a Clone Hero song."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import align as A
from .cookies import CookieError
from .download import DownloadError, fetch_video
from .media import MediaError, decode_audio, require_tools, video_info
from .osutil import cache_dir, configure_console
from .pipeline import (
    SyncOptions, add_render_options, align_song, discard_download, fmt_t, load_cookies, render_and_install,
)
from .songdir import SongError, load_song_folder


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="chvideo",
        description="Download a music video and sync it to a Clone Hero song folder. "
        "The video's soundtrack is aligned against the chart's audio, the result is "
        "rendered so frame t matches song time t, and saved as video.webm in the song folder. "
        "For many songs at once see `chvideo batch --help`; to list songs without a video see "
        "`chvideo missing --help`.",
    )
    p.add_argument("song_dir", type=Path, help="Clone Hero song folder (contains song.ini / notes.* / audio stems)")
    p.add_argument("source", help="Music video URL (anything yt-dlp supports) or a local video file")
    p.add_argument("--offset-ms", type=float, default=None,
                   help="Skip audio analysis and use this fixed offset: video time = song time + offset")
    p.add_argument("--preview", nargs="?", const="", default=None, metavar="PATH",
                   help="Also write a preview (video + chart audio) to check sync in a media player")
    p.add_argument("--analyze-only", action="store_true", help="Only print the alignment, don't render")
    add_render_options(p)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "batch":
        from .batch import main as batch_main

        return batch_main(argv[1:])
    if argv and argv[0] == "missing":
        from .missing import main as missing_main

        return missing_main(argv[1:])
    args = parse_args(argv)
    try:
        return run(args)
    except (SongError, MediaError, A.AlignError, CookieError, DownloadError) as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def run(args: argparse.Namespace) -> int:
    require_tools()
    load_cookies(args)
    song = load_song_folder(args.song_dir)
    print(f"Song:   {song.title}")
    print(f"Folder: {song.path}")
    print(f"Stems:  {', '.join(p.name for p in song.stems)}")

    existing = song.existing_videos()
    if existing and not (args.dry_run or args.analyze_only) and not args.force:
        raise SongError(
            f"This song already has {', '.join(p.name for p in existing)}. Use --force to replace it "
            "(the old file is kept as .bak)."
        )

    opts = SyncOptions.from_args(args)
    t_start = time.time()
    print("\n[1/3] Getting video")
    video_path = fetch_video(args.source, cache_dir(), args.max_height, refresh=args.refresh)
    info = video_info(video_path)
    print(f"  {video_path.name}: {info.width}x{info.height} @ {info.fps:.3f} fps, {fmt_t(info.duration)}")

    print("\n[2/3] Aligning video to song audio")
    vid_audio = decode_audio(video_path, A.SR) if info.has_audio and args.offset_ms is None else None
    result = align_song(song, info, vid_audio, opts)
    if args.analyze_only:
        return 0

    print("\n[3/3] Rendering and installing")
    render_and_install(song, info, result, opts)
    if not args.dry_run and not args.keep_downloads:
        discard_download(video_path, args.source)
    print(f"\nDone in {time.time() - t_start:.0f}s.")
    return 0
