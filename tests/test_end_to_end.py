"""Full runs of `chvideo` and `chvideo batch` on a generated song folder and video.

Paths deliberately contain spaces, '!' and non-ASCII characters, like real Clone Hero libraries.
"""

import json
import re
import subprocess

import numpy as np
import pytest

from chvideo.cli import main

from .conftest import make_video, requires_ffmpeg, synth_music, write_wav

pytestmark = requires_ffmpeg

SR = 44100
OFFSET = 2.0  # the video's music starts 2 s in


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "!Individual Songs"
    folder = root / "Tëst Ártist - Tést Sông (Chärter)"
    folder.mkdir(parents=True)
    song = synth_music(30, sr=SR, seed=7)
    write_wav(folder / "song.wav", song, SR)
    (folder / "notes.chart").write_text("[Song]\n{\n}\n", encoding="utf-8")
    (folder / "song.ini").write_text("[Song]\nartist = Tëst Ártist\nname = Tést Sông\n", encoding="utf-8")

    soundtrack = np.concatenate([synth_music(OFFSET, sr=SR, seed=70), song, synth_music(2, sr=SR, seed=71)])
    write_wav(tmp_path / "soundtrack.wav", soundtrack, SR)
    video = tmp_path / "Tëst Ártist - Tést Sông (Official Video).mkv"
    make_video(video, tmp_path / "soundtrack.wav", len(soundtrack) / SR)
    return root, folder, video


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:format=duration", "-of", "json", str(path)],
        capture_output=True, check=True,
    ).stdout
    info = json.loads(out)
    return info["streams"][0]["codec_name"], float(info["format"]["duration"])


def test_single_song(library, capsys):
    _, folder, video = library
    assert main([str(folder), str(video), "--analyze-only"]) == 0
    offset_ms = float(re.search(r"video offset ([+-][\d.]+) ms", capsys.readouterr().out).group(1))
    assert offset_ms == pytest.approx(OFFSET * 1000, abs=2)

    assert main([str(folder), str(video), "--jobs", "2"]) == 0
    codec, duration = probe(folder / "video.webm")
    assert codec == "vp8"
    assert duration == pytest.approx(30, abs=0.1)
    assert "video_start_time = 0" in (folder / "song.ini").read_text(encoding="utf-8")
    assert (folder / "song.ini.chvideo.bak").exists()
    assert video.exists()  # a user's own local file is never deleted

    # a second run refuses to overwrite without --force, then keeps a backup with it
    assert main([str(folder), str(video), "--jobs", "2"]) == 1
    assert main([str(folder), str(video), "--jobs", "2", "--force"]) == 0
    assert (folder / "video.webm.bak").exists()


def test_batch(library):
    root, folder, video = library
    assert main(["batch", str(video), "--library", str(root), "--plan"]) == 0
    assert not (folder / "video.webm").exists()
    assert main(["batch", str(video), "--library", str(root), "-y", "--jobs", "2"]) == 0
    assert probe(folder / "video.webm")[0] == "vp8"
