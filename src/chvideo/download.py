"""Fetch music videos and their metadata with yt-dlp (downloads are cached per URL)."""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import cookies
from .media import VIDEO_CONTAINER_EXTS
from .osutil import is_local_file


def _deno_path() -> str | None:
    try:
        import deno

        return deno.find_deno_bin()
    except Exception:
        return None


def _base_opts() -> dict:
    opts: dict = {"noplaylist": True}
    if deno := _deno_path():
        opts["js_runtimes"] = {"deno": {"path": deno}}
    return opts


class DownloadError(RuntimeError):
    pass


class _Logger:
    """Route yt-dlp's messages: shown normally unless quiet, but its error lines are dropped
    because they are re-raised as DownloadError with chvideo-specific advice (yt-dlp's own text
    suggests --cookies-from-browser, which chvideo deliberately doesn't offer).  The download
    progress bar is drawn by yt-dlp directly and is unaffected."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet

    def debug(self, msg: str) -> None:
        if not self.quiet and not msg.startswith("[debug] "):
            print(msg.split(" Use --cookies-from-browser")[0])

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        if not self.quiet:
            print(f"WARNING: {msg}")

    def error(self, msg: str) -> None: ...


@contextlib.contextmanager
def _youtube_dl(opts: dict, source: str) -> Iterator:
    import yt_dlp

    with cookies.private_copy() as cookie_path:
        if cookie_path:
            opts = opts | {"cookiefile": cookie_path}
        opts = opts | {"logger": _Logger(bool(opts.get("quiet")))}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                yield ydl
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(_explain(str(e), source)) from None


def _explain(message: str, source: str) -> str:
    msg = message.removeprefix("ERROR: ").split(" Use --cookies")[0].strip()
    low = msg.lower()
    active = cookies.active()
    if "not a bot" in low:
        # YouTube's rate limiting: happens to any video after many requests from one IP.
        advice = "YouTube is rate-limiting this connection; wait a while (often an hour) and retry"
        if active is None:
            advice += ", or pass --cookies <cookies.txt>, which makes this less likely"
        return f"{msg} -> {advice} (see README: Age-restricted videos)"
    if "confirm your age" in low or "sign in" in low or "members-only" in low or "private video" in low:
        if active is None:
            return (f"{msg} -> this video needs a signed-in YouTube account: pass --cookies <cookies.txt> "
                    "(see README: Age-restricted videos)")
        return (f"{msg} -> YouTube didn't accept the sign-in in {active.path}; the cookies are probably expired "
                "or were rotated. Export a fresh cookies.txt (see README: Age-restricted videos)")
    return msg


@dataclass
class VideoMeta:
    source: str  # URL or local path
    title: str
    channel: str = ""
    artist: str = ""  # set by YouTube for many official music videos
    track: str = ""
    duration: float | None = None

    @property
    def label(self) -> str:
        return self.title or self.source


def resolve_source(source: str) -> list[VideoMeta]:
    """Metadata for a URL (playlists are expanded) or a local file, without downloading."""
    if is_local_file(source):
        local = Path(source).expanduser()
        return [VideoMeta(str(local.resolve()), local.stem)]

    opts = _base_opts() | {"quiet": True, "no_warnings": True, "skip_download": True,
                           "extract_flat": "in_playlist"}
    with _youtube_dl(opts, source) as ydl:
        info = ydl.extract_info(source, download=False)
    if info.get("_type") in ("playlist", "multi_video"):
        out = []
        for e in info.get("entries") or []:
            if not e:
                continue
            url = e.get("webpage_url") or e.get("url")
            if not url:
                continue
            if not url.startswith("http") and e.get("ie_key") == "Youtube":
                url = f"https://www.youtube.com/watch?v={url}"
            out.append(_meta(url, e))
        return out
    return [_meta(info.get("webpage_url") or source, info)]


def _meta(url: str, info: dict) -> VideoMeta:
    return VideoMeta(
        source=url,
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        artist=info.get("artist") or (", ".join(info["artists"]) if info.get("artists") else ""),
        track=info.get("track") or "",
        duration=info.get("duration"),
    )


def fetch_video(source: str, cache_dir: Path, max_height: int, *, refresh: bool = False, quiet: bool = False) -> Path:
    """Return a local path for `source` (a URL or an existing file)."""
    if is_local_file(source):
        return Path(source).expanduser().resolve()

    key = hashlib.sha1(f"{source}|{max_height}".encode()).hexdigest()[:16]
    target_dir = cache_dir / "downloads" / key
    target_dir.mkdir(parents=True, exist_ok=True)
    if not refresh:
        done = [p for p in target_dir.iterdir() if p.stem == "video" and p.suffix in VIDEO_CONTAINER_EXTS]
        if done:
            if not quiet:
                print(f"Using cached download: {done[0]}")
            return done[0]

    opts = _base_opts() | {
        # Best video stream up to the requested height plus best audio; the audio is
        # what we align against, the picture gets re-encoded anyway.
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "merge_output_format": "mkv",
        "outtmpl": str(target_dir / "video.%(ext)s"),
        "quiet": quiet,
        "no_warnings": quiet,
        "noprogress": quiet,
        "overwrites": True,
    }
    with _youtube_dl(opts, source) as ydl:
        info = ydl.extract_info(source, download=True)
        title = info.get("title")
    files = [p for p in target_dir.iterdir() if p.stem == "video" and p.suffix in VIDEO_CONTAINER_EXTS]
    if not files:
        raise RuntimeError(f"yt-dlp finished but no video file was found in {target_dir}")
    if title:
        (target_dir / "title.txt").write_text(title, encoding="utf-8")
    return files[0]
