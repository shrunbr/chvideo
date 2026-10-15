"""Netscape-format cookies.txt support for age-restricted / sign-in-only YouTube videos.

See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp and
https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies

The user's file is only ever read: every yt-dlp instance gets its own private temporary copy
because yt-dlp writes the cookie jar back on exit, and batch mode runs several instances at
once (concurrent write-backs would clobber the file).
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

HEADERS = ("# Netscape HTTP Cookie File", "# HTTP Cookie File")
# Cookies that only exist when signed in to a Google/YouTube account.
LOGIN_COOKIES = {"LOGIN_INFO", "SID", "__Secure-1PSID", "__Secure-3PSID", "SAPISID", "__Secure-3PAPISID"}


class CookieError(RuntimeError):
    pass


@dataclass
class CookieFile:
    path: Path
    text: str  # normalised: LF line endings, no BOM
    youtube_cookies: int
    warnings: list[str]


def load_cookie_file(path: Path) -> CookieFile:
    path = path.expanduser()
    if not path.is_file():
        raise CookieError(f"Cookies file not found: {path}")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    # Files exported by a Windows browser have CRLF line endings; yt-dlp on Linux/WSL would
    # keep the '\r' as part of every cookie value (-> "HTTP Error 400: Bad Request").
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        raise CookieError(
            f"{path} is a JSON cookie export. yt-dlp needs the Netscape cookies.txt format; "
            "export again with a cookies.txt extension (see README: Age-restricted videos)."
        )
    first = stripped.split("\n", 1)[0].strip()
    if first not in HEADERS:
        raise CookieError(
            f"{path} doesn't look like a Netscape cookies.txt file: the first line must be "
            f"'{HEADERS[0]}' or '{HEADERS[1]}' (found {first[:60]!r})."
        )

    now = time.time()
    yt, login, login_expired = 0, set(), set()
    for line in text.split("\n"):
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, _, _, _, expires, name, _ = fields
        if not domain.lstrip(".").endswith("youtube.com"):
            continue
        yt += 1
        if name in LOGIN_COOKIES:
            exp = float(expires) if expires.replace(".", "", 1).isdigit() else 0.0
            (login_expired if 0 < exp < now else login).add(name)

    warnings = []
    if yt == 0:
        warnings.append("it contains no youtube.com cookies, so age-restricted videos will still fail")
    elif not login and login_expired:
        warnings.append("its YouTube sign-in cookies have expired; export a fresh cookies.txt")
    elif not login:
        warnings.append("it has no YouTube sign-in cookies (it was exported while signed out?)")
    return CookieFile(path, text, yt, warnings)


_active: CookieFile | None = None


def set_active(cookie_file: CookieFile | None) -> None:
    global _active
    _active = cookie_file


def active() -> CookieFile | None:
    return _active


@contextlib.contextmanager
def private_copy() -> Iterator[str | None]:
    """A temporary copy of the active cookies for one yt-dlp instance (None if no cookies)."""
    if _active is None:
        yield None
        return
    fd, name = tempfile.mkstemp(prefix="chvideo-cookies-", suffix=".txt")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(_active.text)
        yield name
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)
