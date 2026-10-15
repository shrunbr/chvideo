import numpy as np
import pytest

from chvideo import align as A
from chvideo.render import build_timeline

from .conftest import synth_music

SR = A.SR


def s(t):
    return int(t * SR)


def test_single_offset():
    song = synth_music(45, seed=1)
    video = np.concatenate([synth_music(3, seed=99), song, synth_music(2, seed=98)])
    r = A.align(song, video, log=lambda *a: None)
    assert len(r.segments) == 1
    assert r.segments[0].offset == pytest.approx(3.0, abs=0.002)
    assert r.coverage > 0.9


def test_edited_video_is_split_into_sections():
    song = synth_music(60, seed=2)
    # video: 4 s intro, song 0-25, (song 25-32 cut), song 32-45, 3 s skit, song 45-60
    video = np.concatenate([
        synth_music(4, seed=50), song[: s(25)], song[s(32) : s(45)], synth_music(3, seed=51), song[s(45) :],
    ])
    r = A.align(song, video, log=lambda *a: None)
    offsets = [round(seg.offset, 2) for seg in r.segments]
    assert offsets == [4.0, -3.0, 0.0]
    assert r.segments[0].song_end == pytest.approx(25, abs=0.5)
    assert r.segments[1].song_start == pytest.approx(32, abs=0.5)


def test_different_recording_is_not_covered():
    song = synth_music(40, seed=3)
    other = synth_music(40, seed=4)
    try:
        r = A.align(song, other, log=lambda *a: None)
    except A.AlignError:
        return
    assert r.coverage < 0.2


def test_timeline_covers_whole_song():
    seg = A.Segment(song_start=5.0, song_end=50.0, video_start=2.0)  # video starts 3 s after the song
    r = A.AlignResult([seg], song_duration=60.0, video_duration=40.0, coverage=1.0)
    pieces = build_timeline(r, video_duration=40.0)
    assert pieces[0].v0 is None and pieces[0].t1 == pytest.approx(3.0)  # black until video t=0
    assert pieces[-1].v0 is None and pieces[-1].t1 == pytest.approx(60.0)  # black after it ends
    for a, b in zip(pieces, pieces[1:], strict=False):
        assert a.t1 == pytest.approx(b.t0)
