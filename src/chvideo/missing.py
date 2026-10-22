"""`chvideo missing`: list the songs in a library that don't have a video yet."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .library import LibrarySong, scan_library


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="chvideo missing",
        description="Scan a Clone Hero songs folder (recursively) and list the songs that have no video.* file. "
        "The list goes to stdout and the count to stderr, so the output can be redirected to a file.",
    )
    p.add_argument("library", nargs="?", type=Path, default=os.environ.get("CHVIDEO_LIBRARY"),
                   help="Clone Hero songs folder to scan (default: $CHVIDEO_LIBRARY)")
    p.add_argument("--paths", action="store_true",
                   help="Print each song folder's full path instead of 'Artist - Song'")
    return p.parse_args(argv)


def missing_videos(library: list[LibrarySong]) -> list[list[LibrarySong]]:
    """Songs without a video, charts of the same song grouped together, sorted by title."""
    groups: dict[tuple[str, str], list[LibrarySong]] = {}
    for song in library:
        if not song.has_video:
            groups.setdefault(song.key, []).append(song)
    return sorted(groups.values(), key=lambda g: g[0].title.casefold())


def run(args: argparse.Namespace) -> int:
    if args.library is None:
        print("error: pass a songs folder (or set CHVIDEO_LIBRARY)", file=sys.stderr)
        return 2
    library = scan_library(Path(args.library))
    groups = missing_videos(library)
    if args.paths:
        for path in sorted((s.path for g in groups for s in g), key=lambda p: str(p).casefold()):
            print(path)
    else:
        for g in groups:
            print(g[0].title + (f"  ({len(g)} charts)" if len(g) > 1 else ""))
    charts = sum(len(g) for g in groups)
    sys.stdout.flush()  # keep the count after the list when both go to one pipe
    print(f"\n{charts} of {len(library)} song folders have no video ({len(groups)} distinct songs).",
          file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
