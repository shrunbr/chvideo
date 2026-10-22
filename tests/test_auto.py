import argparse
from pathlib import Path

import pytest

from chvideo import align as A
from chvideo import batch
from chvideo.auto import search_query
from chvideo.download import VideoMeta
from chvideo.library import LibrarySong, rank_videos


def song(artist, name, length=None):
    return LibrarySong(Path(f"/lib/{artist} - {name}"), artist, name, False, length)


def video(title, channel, duration=200, views=1000, verified=False):
    return VideoMeta(f"https://youtu.be/{title}", title, channel, duration=duration, views=views, verified=verified)


# Real search results for "Wolfmother Woman official music video"
WOLFMOTHER = [
    video("Wolfmother - Woman (Official Video)", "WolfmotherMusic", 182, 7374475, True),
    video("Wolfmother - Woman (Audio)", "WolfmotherMusic", 177, 990965, True),
    video("Wolfmother - Woman (Lyrics)", "aNewHorizonAbove", 177, 3935495),
    video("Wolfmother - Woman, Live Music Hall, Colonia 2996", "kigonjiro", 414, 510485),
    video("Wolfmother - Woman", "WolfmotherMusic", 148, 672763, True),
    video("Wolfmother - Woman", "Wolfmother - Topic", 177, 50000),
    video("Wolfmother - Joker & the Thief (Official Video)", "WolfmotherMusic", 280, 9000000, True),
]


def test_rank_videos_prefers_official_video_and_drops_other_versions():
    ranked = rank_videos(song("Wolfmother", "Woman", 177.0), WOLFMOTHER)
    assert ranked[0].title == "Wolfmother - Woman (Official Video)"
    titles = [m.title for m in ranked]
    assert not any(w in t for t in titles for w in ("Audio", "Lyrics", "Live", "Joker"))
    assert all(m.channel != "Wolfmother - Topic" for m in ranked)


def test_rank_videos_live_chart_needs_live_video():
    results = [
        video("AC/DC - Back In Black (Official 4K Video)", "AC/DC", 255, 900000000, True),
        video("AC/DC - Back In Black (Live At River Plate, December 2009)", "AC/DC", 270, 50000000, True),
    ]
    live = song("AC/DC", "Back in Black (Live)")
    assert [m.title for m in rank_videos(live, results)] == [results[1].title]
    assert "official" not in search_query(live)
    # the band Live isn't a live version
    assert rank_videos(song("Live", "Lightning Crashes"), [video("Live - Lightning Crashes (Official Video)", "Live")])


def test_rank_videos_rejects_videos_too_short_for_the_song():
    s = song("Band", "Song", 240.0)
    assert rank_videos(s, [video("Band - Song (Official Video)", "Band", duration=100)]) == []
    assert rank_videos(s, [video("Band - Song (Official Video)", "Band", duration=3600)]) == []


def test_sync_falls_back_to_next_search_result(monkeypatch, tmp_path):
    first, second = video("Band - Song (Official Video)", "Band"), video("Band - Song", "Band")
    job = batch.Job(first, explicit=[song("Band", "Song")], alternatives=[second])
    synced, discarded = [], []

    def fake_sync(job, video_path, opts, args):
        if video_path.name == "first.mkv":
            raise A.AlignError("The song's audio could not be matched anywhere in the video.")
        synced.append(job.meta)

    monkeypatch.setattr(batch, "_sync_job", fake_sync)
    monkeypatch.setattr(batch, "fetch_video", lambda source, *a, **k: tmp_path / "second.mkv")
    monkeypatch.setattr(batch, "discard_download", lambda path, source: discarded.append(path.name))
    args = argparse.Namespace(max_height=1080, min_height=None, refresh=False)

    path = batch._sync_with_alternatives(job, lambda: tmp_path / "first.mkv", None, args, tmp_path)
    assert path.name == "second.mkv"
    assert synced == [second] and job.meta is second
    assert discarded == ["first.mkv"]


def test_sync_raises_when_no_alternative_left(monkeypatch, tmp_path):
    job = batch.Job(video("Band - Song", "Band"), explicit=[song("Band", "Song")])

    def fail(*a):
        raise A.AlignError("no match")

    monkeypatch.setattr(batch, "_sync_job", fail)
    with pytest.raises(A.AlignError):
        batch._sync_with_alternatives(job, lambda: tmp_path / "v.mkv", None, argparse.Namespace(), tmp_path)


def _library(root, *songs):
    for folder, artist, name in songs:
        d = root / folder
        d.mkdir(parents=True)
        (d / "song.opus").write_bytes(b"x")
        (d / "song.ini").write_text(f"[Song]\nartist = {artist}\nname = {name}\n", encoding="utf-8")


def test_blocked_songs_are_skipped_and_failures_can_be_blocked(tmp_path, monkeypatch, capsys):
    from chvideo import auto

    root = tmp_path / "Songs"
    _library(root, ("Pack/Band - Song", "Band", "Song"), ("Other - Thing", "Other", "Thing"))
    root = root.resolve()
    monkeypatch.setattr(auto, "resolve_source", lambda q: [])  # nothing found for anything
    args = auto.parse_args([str(root), "--block-failed"])

    jobs = auto._plan(args, root)
    assert [j.detail for j in jobs] == ["no music video found in the search results"] * 2
    assert all(j.permanent for j in jobs)
    auto._offer_block(jobs, root, args)
    text = (root / auto.BLOCKLIST).read_text(encoding="utf-8")
    assert "Pack/Band - Song\n" in text and "Other - Thing\n" in text

    assert auto._plan(args, root) == []
    assert "2 of them blocked" in capsys.readouterr().out
    assert len(auto._plan(auto.parse_args([str(root), "--retry-blocked"]), root)) == 2

    # unblock by deleting the line
    (root / auto.BLOCKLIST).write_text(text.replace("Other - Thing\n", ""), encoding="utf-8")
    assert [j.explicit[0].name for j in auto._plan(args, root)] == ["Thing"]


def test_only_permanent_failures_are_offered_for_blocking(tmp_path):
    from chvideo import auto

    jobs = [
        batch.Job(video("a", "x"), explicit=[song("A", "a")], status="fail", detail="rate limited"),
        batch.Job(video("b", "x"), explicit=[song("B", "b")], status="fail", detail="no match", permanent=True),
    ]
    for j in jobs:
        j.explicit[0].path = tmp_path / j.explicit[0].name
    auto._offer_block(jobs, tmp_path, auto.parse_args([str(tmp_path), "--block-failed"]))
    lines = (tmp_path / auto.BLOCKLIST).read_text(encoding="utf-8").splitlines()
    assert "b" in lines and "a" not in lines

    # nothing is blocked while songs are still pending (declined, --plan, interrupted)
    jobs.append(batch.Job(video("c", "x"), explicit=[song("C", "c")]))
    jobs[1].explicit[0].path = tmp_path / "c2"
    auto._offer_block(jobs, tmp_path, auto.parse_args([str(tmp_path), "--block-failed"]))
    assert "c2" not in (tmp_path / auto.BLOCKLIST).read_text(encoding="utf-8")


def meta(title, channel, description="", verified=False):
    return VideoMeta(f"https://youtu.be/{title}", title, channel, duration=200, description=description,
                     verified=verified)


WOLVES = song("Bad Wolves", "Officer Down")


@pytest.mark.parametrize("video, verdict", [
    # Real Officer Down results: a hired drummer's playthrough, a fan re-upload, an auto-generated track
    (meta('Alex Rudinger - Bad Wolves - "Officer Down"', "Alex Rüdinger", "I was recently contacted / hired by "
          "Bad Wolves to learn their set list. Their drummer, John Boecklin, hurt his", verified=True),
     "the uploader's own performance"),
    (meta("Bad Wolves - Officer Down", "David Rolfe's Rock & Metal"), "doesn't look official (fan re-upload?)"),
    (meta("Officer Down", "Bad Wolves", "Provided to YouTube by IIP-DDS Officer Down", verified=True),
     "auto-generated audio upload"),
    # "Music video" in a fan's title isn't enough, "Official" from a fan with a disclaimer neither
    (meta("Bad Wolves - Officer Down Music Video", "fan123"), "doesn't look official (fan re-upload?)"),
    (meta("Bad Wolves - Officer Down (Official Video)", "fan123", "ALL RIGHTS GO TO ORIGINAL OWNERS"),
     "fan upload or cover"),
    (meta("Bad Wolves - Officer Down (Unofficial Music Video)", "fan123"), "unofficial video"),
    (meta("Bad Wolves - Officer Down (Student Made Video)", "fan123"), "title says 'student'"),
    # Legitimate: the artist's channel with a plain title, a label, a director's upload with credits
    (meta("Bad Wolves - Officer Down", "Bad Wolves", verified=True), None),
    (meta('Bad Wolves - "Officer Down"', "Better Noise Records"), None),
    (meta("Bad Wolves - Officer Down", "Jane Doe Films", "Directed by Jane Doe\nDOP: John Roe"), None),
    (meta("Jane Doe - Bad Wolves - Officer Down", "Jane Doe", "Music video for Bad Wolves. Directed by Jane Doe"), None),
    # Official descriptions often link a playthrough or credit cover art: not a reason to reject
    (meta("Bad Wolves - Officer Down (Official Video)", "Bad Wolves",
          "Watch the guitar playthrough here. Album cover art by Remo Evans."), None),
])
def test_judge_video(video, verdict):
    from chvideo.library import judge_video

    assert judge_video(WOLVES, video) == verdict


def test_allow_unofficial_keeps_fan_reuploads_but_not_covers():
    from chvideo.library import judge_video

    assert judge_video(WOLVES, meta("Bad Wolves - Officer Down Music Video", "fan123"), unofficial=True) is None
    assert judge_video(WOLVES, meta("Bad Wolves - Officer Down (Drum Cover)", "fan123"), unofficial=True)


def test_find_videos_checks_full_metadata_only_for_untrusted_uploaders(monkeypatch):
    from chvideo import auto

    search = [meta("Bad Wolves - Officer Down (Official Video)", "Bad Wolves", verified=True),
              meta("Bad Wolves - Officer Down", "Jane Doe Films")]
    full = {search[1].source: meta("Bad Wolves - Officer Down", "Jane Doe Films", "Directed by Jane Doe")}
    fetched = []

    def resolve(source):
        if source.startswith("ytsearch"):
            return search
        fetched.append(source)
        return [full[source]]

    monkeypatch.setattr(auto, "resolve_source", resolve)
    picks, _, _ = auto.find_videos(WOLVES, auto.parse_args(["lib"]))
    assert [p.channel for p in picks] == ["Bad Wolves", "Jane Doe Films"]
    assert fetched == [search[1].source]  # the artist's own upload needed no extra request


def test_plan_stops_cleanly_when_rate_limited(tmp_path, monkeypatch, capsys):
    from chvideo import auto
    from chvideo.download import RateLimited

    root = tmp_path / "Songs"
    _library(root, ("A", "Band", "Alpha"), ("B", "Band", "Beta"), ("C", "Band", "Gamma"))
    found = meta("Band - Alpha (Official Video)", "Band")

    def find(song, args):
        if song.name == "Alpha":
            return [found], "", False
        raise RateLimited("Sign in to confirm you're not a bot")

    monkeypatch.setattr(auto, "find_videos", find)
    jobs = auto._plan(auto.parse_args([str(root)]), root.resolve())
    assert [j.meta for j in jobs] == [found]  # nothing marked failed (or blockable) for the rest
    assert "rate-limiting after 1 of 3 songs" in capsys.readouterr().out


def test_format_selector_respects_min_height():
    from chvideo.download import format_selector

    assert format_selector(1080) == "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
    sel = format_selector(1080, 720)
    assert all("[height>=?720]" in alt for alt in sel.split("/"))  # no fallback below 720p


def test_check_min_height():
    from chvideo.download import LowResolution
    from chvideo.media import VideoInfo
    from chvideo.pipeline import check_min_height

    info = VideoInfo(Path("v.mkv"), 640, 360, 30, 200.0, 0.0, True)
    check_min_height(info, None)
    check_min_height(info, 360)
    with pytest.raises(LowResolution, match="360p, below --min-height 480"):
        check_min_height(info, 480)


def test_min_height_must_not_exceed_max_height(capsys):
    from chvideo import auto

    assert auto.parse_args(["lib", "--min-height", "720"]).min_height == 720
    with pytest.raises(SystemExit):
        auto.parse_args(["lib", "--min-height", "1440", "--max-height", "1080"])
    assert "--min-height 1440 must be between" in capsys.readouterr().err


def test_too_low_res_video_falls_back_to_next_search_result(monkeypatch, tmp_path):
    from chvideo.download import LowResolution

    first, second = video("Band - Song (Official Video)", "Band"), video("Band - Song", "Band")
    job = batch.Job(first, explicit=[song("Band", "Song")], alternatives=[second])

    def first_download():
        raise LowResolution("the video isn't available in 720p or better (--min-height)")

    monkeypatch.setattr(batch, "_sync_job", lambda *a: None)
    monkeypatch.setattr(batch, "fetch_video", lambda source, *a, **k: tmp_path / "second.mkv")
    args = argparse.Namespace(max_height=1080, min_height=720, refresh=False)
    assert batch._sync_with_alternatives(job, first_download, None, args, tmp_path).name == "second.mkv"
    assert job.meta is second
