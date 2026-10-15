from pathlib import Path

from chvideo.batch import _read_inputs
from chvideo.download import VideoMeta
from chvideo.library import LibrarySong, candidates, normalize, scan_library


def song(artist, name):
    return LibrarySong(Path(f"/lib/{artist} - {name}"), artist, name, False)


def test_normalize_drops_junk_brackets_only():
    assert normalize("Kryptonite (Official Music Video) [HD]") == "kryptonite"
    assert normalize("Thunderstruck (Live)") == "thunderstruck live"
    assert normalize("AC／DC") == normalize("AC/DC") == "ac dc"
    assert normalize("Don't Stop (Remastered 2011)") == "dont stop"


def test_candidates_prefer_right_song_over_name_substring():
    lib = [song("Santana", "Smooth"), song("Alien Ant Farm", "Smooth Criminal (Michael Jackson cover)")]
    meta = VideoMeta("x", "Alien Ant Farm - Smooth Criminal (Official Music Video)", "AlienAntFarmVEVO")
    best = candidates(meta, lib)[0]
    assert best.title.startswith("Alien Ant Farm")


def test_candidates_group_duplicate_charts_and_reject_unrelated():
    lib = [song("Papa Roach", "Scars"), song("Papa Roach", "Scars"), song("Skillet", "Hero")]
    cands = candidates(VideoMeta("x", "Papa Roach - Scars (Official HD Video)", "PapaRoachVEVO"), lib)
    assert len(cands[0].songs) == 2
    assert candidates(VideoMeta("x", "Linkin Park - Numb", "Linkin Park"), lib) == []


def test_scan_library(tmp_path):
    root = tmp_path / "!Individual Songs"
    a = root / "Pack" / "Artist - Song (Charter)"
    b = root / "Other - Thing"
    for d in (a, b):
        d.mkdir(parents=True)
        (d / "song.opus").write_bytes(b"x")
    (a / "song.ini").write_text("[Song]\nartist = Artist\nname = Song\n", encoding="utf-8")
    (a / "video.webm").write_bytes(b"x")
    (b / "notes.chart").write_text("x")
    (root / "not a song").mkdir()
    songs = {s.path.name: s for s in scan_library(root)}
    assert set(songs) == {"Artist - Song (Charter)", "Other - Thing"}
    assert songs["Artist - Song (Charter)"].has_video
    assert (songs["Other - Thing"].artist, songs["Other - Thing"].name) == ("Other", "Thing")


def test_read_inputs(tmp_path):
    links = tmp_path / "links.txt"
    links.write_text(
        "# comment\n\nhttps://youtu.be/a\nhttps://youtu.be/b | Some Folder\nhttps://youtu.be/c\tTab Folder\n",
        encoding="utf-8",
    )
    got = _read_inputs([str(links), "ytsearch1:artist song"])
    assert got == [
        ("https://youtu.be/a", None),
        ("https://youtu.be/b", "Some Folder"),
        ("https://youtu.be/c", "Tab Folder"),
        ("ytsearch1:artist song", None),
    ]
