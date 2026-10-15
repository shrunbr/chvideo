"""Small platform helpers so chvideo behaves the same on Windows, Linux and macOS."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def cache_dir() -> Path:
    """Where downloads and work files go.

    $CHVIDEO_CACHE overrides it; otherwise each OS's usual per-user cache location:
    Windows %LOCALAPPDATA%\\chvideo\\Cache, macOS ~/Library/Caches/chvideo,
    Linux $XDG_CACHE_HOME/chvideo (default ~/.cache/chvideo).
    """
    if override := os.environ.get("CHVIDEO_CACHE"):
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "chvideo" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "chvideo"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "chvideo"


def configure_console() -> None:
    """Never crash on printing a song or video title.

    When output goes to a pipe or file on Windows, Python encodes it with the legacy code page
    (e.g. cp1252), which can't represent titles like "AC／DC" or "…" and would raise
    UnicodeEncodeError mid-run.  Unencodable characters are replaced instead.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def is_local_file(source: str) -> bool:
    """True if `source` is an existing local file, never for URLs or yt-dlp search strings.

    (On Windows, strings like "https://..." or "ytsearch1:..." look like drive-relative or
    invalid paths, so they must not reach the filesystem checks.)
    """
    if "://" in source or source.lower().startswith(("ytsearch", "ytdl:")):
        return False
    try:
        return Path(source).expanduser().is_file()
    except (OSError, ValueError):
        return False


def read_text_any(path: Path) -> str:
    """Read a user-written text file: UTF-8 (with or without BOM), else the Windows legacy
    code page that older editors such as Notepad used to save in."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def ffmpeg_install_hint() -> str:
    if sys.platform == "win32":
        return "Install it with `winget install Gyan.FFmpeg`, then open a new terminal."
    if sys.platform == "darwin":
        return "Install it with `brew install ffmpeg`."
    return "Install it with your package manager, e.g. `sudo apt install ffmpeg`."
