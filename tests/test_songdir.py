import pytest

from chvideo.songdir import SongError, load_song_folder, read_ini_values, set_ini_value


def test_set_ini_value_preserves_crlf_bom_and_other_lines(tmp_path):
    ini = tmp_path / "song.ini"
    original = "﻿[Song]\r\nname = Kryptonite\r\nartist = 3 Doors Down\r\nVideo_Start_Time = -2000\r\n"
    ini.write_bytes(original.encode("utf-8"))
    set_ini_value(ini, "video_start_time", "0")
    raw = ini.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\nvideo_start_time = 0\r\n" in raw
    assert b"-2000" not in raw
    assert b"name = Kryptonite\r\n" in raw
    # original backed up exactly once
    backup = tmp_path / "song.ini.chvideo.bak"
    assert backup.read_bytes() == original.encode("utf-8")
    set_ini_value(ini, "video_start_time", "5")
    assert backup.read_bytes() == original.encode("utf-8")


def test_set_ini_value_inserts_into_song_section(tmp_path):
    ini = tmp_path / "song.ini"
    ini.write_text("[song]\nname = X\n\n[other]\nfoo = 1\n", encoding="utf-8")
    set_ini_value(ini, "video_start_time", "0")
    lines = ini.read_text(encoding="utf-8").splitlines()
    assert lines.index("video_start_time = 0") < lines.index("[other]")
    assert read_ini_values(ini)["video_start_time"] == "0"


def test_cp1252_ini(tmp_path):
    ini = tmp_path / "song.ini"
    ini.write_bytes("[Song]\nartist = Beyoncé\n".encode("cp1252"))
    set_ini_value(ini, "video_start_time", "0")
    assert read_ini_values(ini)["artist"] == "Beyoncé"


def test_load_song_folder_stems(tmp_path):
    for name in ("song.opus", "guitar.ogg", "crowd.opus", "preview.ogg", "drums_1.opus", "notes.mid"):
        (tmp_path / name).write_bytes(b"x")
    song = load_song_folder(tmp_path)
    assert sorted(p.name for p in song.stems) == ["drums_1.opus", "guitar.ogg", "song.opus"]


def test_load_song_folder_without_audio(tmp_path):
    (tmp_path / "notes.chart").write_text("x")
    with pytest.raises(SongError):
        load_song_folder(tmp_path)
