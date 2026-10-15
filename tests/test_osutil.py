import io
import sys
from pathlib import Path

from chvideo import osutil


def test_cache_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CHVIDEO_CACHE", str(tmp_path / "x"))
    assert osutil.cache_dir() == tmp_path / "x"


def test_cache_dir_windows(monkeypatch):
    monkeypatch.delenv("CHVIDEO_CACHE")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    assert osutil.cache_dir() == Path(r"C:\Users\me\AppData\Local") / "chvideo" / "Cache"


def test_cache_dir_linux(monkeypatch):
    monkeypatch.delenv("CHVIDEO_CACHE")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg")
    assert osutil.cache_dir() == Path("/tmp/xdg") / "chvideo"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert osutil.cache_dir() == Path.home() / ".cache" / "chvideo"


def test_cache_dir_macos(monkeypatch):
    monkeypatch.delenv("CHVIDEO_CACHE")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert osutil.cache_dir() == Path.home() / "Library" / "Caches" / "chvideo"


def test_console_survives_unencodable_titles(monkeypatch):
    # What Windows gives Python when output is redirected to a file or pipe.
    buf = io.BytesIO()
    legacy = io.TextIOWrapper(buf, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", legacy)
    osutil.configure_console()
    print("AC／DC - Thunderstruck … ✓")  # would raise UnicodeEncodeError without the fix
    legacy.flush()
    assert buf.getvalue().startswith(b"AC?DC - Thunderstruck")


def test_is_local_file(tmp_path):
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x")
    assert osutil.is_local_file(str(f))
    assert not osutil.is_local_file(str(tmp_path / "missing.mkv"))
    assert not osutil.is_local_file("https://www.youtube.com/watch?v=abc&list=x")
    assert not osutil.is_local_file("ytsearch1:Artist Song official video")
    assert not osutil.is_local_file("C:bad|name?*")


def test_read_text_any(tmp_path):
    p = tmp_path / "links.txt"
    p.write_bytes("Beyoncé – Halo\n".encode("cp1252"))  # old Notepad "ANSI"
    assert osutil.read_text_any(p) == "Beyoncé – Halo\n"
    p.write_bytes("\ufeffAC／DC\n".encode("utf-8"))  # UTF-8 with BOM
    assert osutil.read_text_any(p) == "AC／DC\n"
