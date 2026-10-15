import os
import time
from pathlib import Path

import pytest

from chvideo import cookies

GOOD = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t{exp}\tLOGIN_INFO\tabc\n"


def test_crlf_file_is_normalised_and_private_copy_cleaned_up(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_bytes(GOOD.format(exp=int(time.time()) + 10**7).replace("\n", "\r\n").encode())
    before = f.read_bytes()
    cf = cookies.load_cookie_file(f)
    assert "\r" not in cf.text and cf.youtube_cookies == 1 and cf.warnings == []
    cookies.set_active(cf)
    try:
        with cookies.private_copy() as path:
            assert path and Path(path).read_bytes() == cf.text.encode()
        assert not os.path.exists(path)
    finally:
        cookies.set_active(None)
    assert f.read_bytes() == before  # the user's file is never modified


def test_json_export_rejected(tmp_path):
    f = tmp_path / "cookies.json"
    f.write_text('[{"domain": ".youtube.com"}]')
    with pytest.raises(cookies.CookieError, match="JSON"):
        cookies.load_cookie_file(f)


def test_bad_header_rejected(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text("domain\tflag\n")
    with pytest.raises(cookies.CookieError, match="Netscape"):
        cookies.load_cookie_file(f)


def test_warnings(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text(GOOD.format(exp=1600000000))
    assert "expired" in cookies.load_cookie_file(f).warnings[0]
    f.write_text("# HTTP Cookie File\n.google.com\tTRUE\t/\tTRUE\t0\tNID\t1\n")
    assert "no youtube.com" in cookies.load_cookie_file(f).warnings[0]


def test_no_cookies_means_no_copy():
    cookies.set_active(None)
    with cookies.private_copy() as path:
        assert path is None
