"""Scan a Clone Hero library and match music-video titles to songs."""

from __future__ import annotations

import math
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from .download import VideoMeta
from .songdir import AUDIO_EXTS, STEM_NAMES, VIDEO_EXTS, read_ini_values

CHART_FILES = {"notes.chart", "notes.mid", "song.ini"}
STEM_FILES = {n + e for n in STEM_NAMES for e in AUDIO_EXTS}
VIDEO_FILES = {"video" + e for e in VIDEO_EXTS}

# Bracketed title parts made only of these words are noise: "(Official Music Video)", "[HD]",
# "(Remastered 2011)", ...  Anything else in brackets ("(Live)", "(feat. X)") is kept.
JUNK = {
    "official", "music", "video", "videos", "lyric", "lyrics", "audio", "hd", "hq", "4k", "1080p", "720p",
    "remaster", "remastered", "remastering", "visualizer", "visualiser", "explicit", "clean", "mv", "vevo",
    "uncensored", "version", "videoclip", "clip", "high", "quality", "definition", "full", "upgrade",
    "director", "directors", "cut", "the", "a", "with", "in", "from", "new", "original", "promo", "film",
}
CHANNEL_NOISE = re.compile(r"vevo|official|topic|music|tv|channel|band", re.IGNORECASE)
# A search result whose title says one of these is something other than the music video itself,
# unless the chart is that version too (a "(Live)" chart, a cover, ...).
NOT_THE_VIDEO = re.compile(
    r"\b(lyrics?|letra|audio|visuali[sz]er|karaoke|instrumental|cover|reacts?|reaction|first time|hears|lesson|"
    r"tutorial|how to play|tabs?|remix|slowed|sped up|nightcore|8d|bass boosted|mashup|parody|tribute|live|"
    r"acoustic|unplugged|demo|rehearsal|guitar hero|rock band|clone hero|full album|interview|making of|"
    r"behind the scenes|track.by.track|commentary|story behind|explained|review|documentary|fan ?made|fan video|amv|"
    r"student|school project|"
    # someone playing along: drum covers, guitar playthroughs, isolated tracks, ...
    r"drums?|drummer|drumcam|guitars?|guitarist|bass|bassist|vocals?|vocalist|piano|playthrough|play through|"
    r"one take|isolated|backing track)\b"
)
# Fan-filmed concerts: "Song @ Venue", "Live 10.14.2018"
CONCERT = re.compile(r"\s@\s|\b\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\b")
# Titles and channels that suggest the official upload rather than a fan's.  (Just "music video"
# doesn't: fan re-uploads say that too.)
OFFICIAL_TITLE = re.compile(r"\b(official|oficial|officiel)\b")
UNOFFICIAL_TITLE = re.compile(r"\bunofficial\b|\bno oficial\b|\bnot? official\b")
LABEL_CHANNEL = re.compile(r"vevo|records|recordings|entertainment", re.IGNORECASE)
# Descriptions of official videos credit the people who made them.  Many official videos are on a
# director's or production company's channel rather than the artist's or label's.
OFFICIAL_DESCRIPTION = re.compile(
    r"music video (by|for)|official (music )?video|directed by|\bdirector\s*[:\-]|\bdir(\.|:)|"
    r"production company|\bproduced by|cinematograph|director of photography|\bdop\s*:|\bedited by",
    re.IGNORECASE,
)
# Descriptions of fan re-uploads and covers.  Kept to phrases an official video's description
# wouldn't use: those often link "the guitar playthrough", credit "cover art by", ...
FAN_DESCRIPTION = re.compile(
    r"all rights (go|goes|belong|belongs|reserved) to|i (do not|don'?t) own|no copyright infringement|"
    r"not affiliated|fan.?made|unofficial|\b(drum|guitar|bass|vocal|piano|keyboard) cover\b|"
    r"\bmy (cover|version|take)\b|\bi (played|recorded|tracked|filmed|learned|covered)\b|"
    r"\b(zildjian|sabian|meinl|paiste|vic firth|promark|mapex|ernie ball|seymour duncan|neural dsp|drumeo)\b",
    re.IGNORECASE,
)
# A chart of one of these versions only matches a video of that version (a studio video's audio
# isn't in a "(Live)" chart), so the video's title must say so too.
VERSIONS = {"live", "acoustic", "unplugged", "remix", "demo", "instrumental"}


@dataclass
class LibrarySong:
    path: Path
    artist: str
    name: str
    has_video: bool
    length: float | None = None  # seconds, from song.ini's song_length

    @property
    def key(self) -> tuple[str, str]:
        """Same song charted by different people shares a key."""
        return normalize(self.artist), normalize(self.name)

    @property
    def title(self) -> str:
        return f"{self.artist} - {self.name}" if self.artist else self.name


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("&", " and ")
    s = re.sub(r"['’`´]", "", s)

    def bracket(m: re.Match) -> str:
        words = re.findall(r"\w+", m.group(1))
        return " " if words and all(w in JUNK or w.isdigit() for w in words) else f" {m.group(1)} "

    s = re.sub(r"[(\[{]([^)\]}]*)[)\]}]", bracket, s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " ".join(s.split())


def _nospace(s: str) -> str:
    return s.replace(" ", "")


def strip_annotations(s: str) -> str:
    """'Toxic (Britney Spears cover)' -> 'Toxic', 'Black Sabbath (WaveGroup)' -> 'Black Sabbath'.
    Only trailing brackets, so '(Don't Fear) The Reaper' keeps its name."""
    while (bare := re.sub(r"\s*[(\[{][^)\]}]*[)\]}]\s*$", "", s)) != s and bare:
        s = bare
    return s


def _split_folder_name(name: str) -> tuple[str, str]:
    """'Artist - Song (Charter)' -> ('Artist', 'Song'); used only when song.ini lacks tags."""
    name = re.sub(r"\s*[(\[][^)\]]*[)\]]\s*$", "", name)
    artist, sep, title = name.partition(" - ")
    return (artist, title) if sep else ("", name)


def _length(ini: dict[str, str]) -> float | None:
    try:
        ms = float(ini.get("song_length", ""))
    except ValueError:
        return None
    return ms / 1000 if ms > 0 else None


def scan_library(root: Path) -> list[LibrarySong]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Library folder not found: {root}")
    songs: list[LibrarySong] = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        files = {e.name.lower(): e for e in entries if e.is_file()}
        if files.keys() & CHART_FILES and files.keys() & STEM_FILES:
            vals = read_ini_values(Path(files["song.ini"].path)) if "song.ini" in files else {}
            artist, name = vals.get("artist", ""), vals.get("name", "")
            if not name:
                artist, name = _split_folder_name(Path(d).name)
            songs.append(LibrarySong(Path(d), artist, name, bool(files.keys() & VIDEO_FILES), _length(vals)))
            continue  # songs don't nest
        stack.extend(Path(e.path) for e in entries if e.is_dir())
    return songs


def score(song: LibrarySong, meta: VideoMeta) -> float:
    """0-100 similarity between a library song and a video's metadata."""
    title = normalize(meta.title)
    artist, name = song.key
    channel = normalize(CHANNEL_NOISE.sub(" ", meta.channel))

    artist_sc = 0.0
    if artist:
        artist_sc = max(
            fuzz.token_set_ratio(artist, title),
            fuzz.ratio(_nospace(artist), _nospace(channel)) if channel else 0,
            fuzz.token_set_ratio(artist, normalize(meta.artist)) if meta.artist else 0,
        )
    # Chart names often carry annotations like "(Michael Jackson cover)" or "(Live)": also
    # compare the bare name, slightly penalised so an exact/studio chart still ranks first.
    core = normalize(re.sub(r"[(\[{][^)\]}]*[)\]}]", " ", song.name))
    names = {name: 0.0, core: 5.0} if core and core != name else {name: 0.0}
    track = normalize(meta.track)
    name_sc = max(
        max(fuzz.token_set_ratio(n, title), fuzz.ratio(n, track) if track else 0) - penalty
        for n, penalty in names.items()
    )
    if not artist:
        return name_sc * 0.9
    return 0.55 * name_sc + 0.45 * artist_sc


def chart_version(song: LibrarySong) -> set[str]:
    """{"live"} for "Song (Live)", etc.; empty for a studio chart."""
    return VERSIONS & set(normalize(song.name).split())


def _artist_channel(song: LibrarySong, meta: VideoMeta) -> bool:
    """Uploaded by the artist themselves ("Slipknot", "WolfmotherMusic", "AlienAntFarmVEVO")."""
    artist = _nospace(normalize(song.artist))
    channels = {_nospace(normalize(meta.channel)), _nospace(normalize(CHANNEL_NOISE.sub(" ", meta.channel)))}
    return bool(artist) and any(c and fuzz.ratio(artist, c) >= 85 for c in channels)


def rejection(song: LibrarySong, meta: VideoMeta) -> str | None:
    """Why this video is clearly not the song's music video, or None.  Only uses signals that are
    reliable on a search result's partial metadata (title, channel, start of the description)."""
    own_words = f" {normalize(song.artist)} {normalize(song.name)} "  # the band Live, a "(Live)" chart, ...
    title = meta.title.lower()
    versions = {m.group(1) for m in NOT_THE_VIDEO.finditer(title)}
    if "video" in title:
        versions.discard("audio")  # "(Official Video) [HQ Audio]" is still the video
    if other := sorted(v for v in versions if f" {v} " not in own_words):
        return f"title says {other[0]!r}"  # lyric video, live version, drum cover, ...
    if CONCERT.search(title):
        return "fan-filmed concert"
    if not chart_version(song) <= set(normalize(meta.title).split()):
        return "not the chart's version"  # the studio video for a "(Live)" chart, ...
    if meta.channel.endswith(" - Topic") or meta.description.startswith("Provided to YouTube by"):
        return "auto-generated audio upload"  # a still picture with the album audio
    if FAN_DESCRIPTION.search(meta.description):
        return "fan upload or cover"
    channel = normalize(meta.channel)
    if (len(_nospace(channel)) >= 4 and f" {channel} " in f" {normalize(meta.title)} "
            and f" {channel} " not in own_words and not _artist_channel(song, meta)
            and not OFFICIAL_DESCRIPTION.search(meta.description)):
        return "the uploader's own performance"  # "Alex Rudinger - Bad Wolves - Officer Down"
    if meta.duration and meta.duration > 20 * 60:
        return "too long (a compilation?)"
    if meta.duration and song.length and meta.duration < song.length / 2:
        return "too short to contain the song"
    return None


def trusted_uploader(song: LibrarySong, meta: VideoMeta) -> bool:
    """The artist's or a label's channel: an upload there that passes `rejection` is legitimate."""
    return _artist_channel(song, meta) or bool(LABEL_CHANNEL.search(meta.channel))


def judge_video(song: LibrarySong, meta: VideoMeta, *, unofficial: bool = False) -> str | None:
    """Why this video isn't the song's music video, or None if it looks legitimate.  Meant for a
    video's full metadata: an official video is uploaded by the artist, their label, or whoever
    made it (a director, production company, ...), and says so in its title or description."""
    if reason := rejection(song, meta):
        return reason
    title = meta.title.lower()
    if UNOFFICIAL_TITLE.search(title):
        return "unofficial video"
    if unofficial or trusted_uploader(song, meta):
        return None
    if OFFICIAL_TITLE.search(title) or OFFICIAL_DESCRIPTION.search(meta.description):
        return None
    return "doesn't look official (fan re-upload?)"


def rank_videos(song: LibrarySong, results: list[VideoMeta], min_score: float = 80.0) -> list[VideoMeta]:
    """Search results that could be this song's music video, most likely first.  Check the ones
    you use with `judge_video` on their full metadata."""
    ranked = []
    for meta in results:
        if rejection(song, meta) or (sc := score(song, meta)) < min_score:
            continue
        title = meta.title.lower()
        sc += 10 if "official" in title and "video" in title else 5 if "video" in title else 0
        sc += 5 if meta.verified else 0
        sc += 10 if _artist_channel(song, meta) else 0
        sc += math.log10(meta.views) if meta.views else 0  # tie-breaker: the real video is usually the popular one
        ranked.append((sc, meta))
    return [m for _, m in sorted(ranked, key=lambda r: r[0], reverse=True)]


@dataclass
class Candidate:
    songs: list[LibrarySong]  # every chart of this song in the library
    score: float

    @property
    def title(self) -> str:
        return self.songs[0].title


def candidates(meta: VideoMeta, library: list[LibrarySong], limit: int = 3, floor: float = 70.0) -> list[Candidate]:
    """Best-matching songs for a video, charts of the same song grouped together."""
    groups: dict[tuple[str, str], Candidate] = {}
    for song in library:
        sc = score(song, meta)
        if sc < floor:
            continue
        g = groups.get(song.key)
        if g is None:
            groups[song.key] = Candidate([song], sc)
        else:
            g.songs.append(song)
            g.score = max(g.score, sc)
    ranked = sorted(groups.values(), key=lambda c: c.score, reverse=True)
    return ranked[:limit]


def resolve_folder(spec: str, library_root: Path | None, library: list[LibrarySong]) -> list[LibrarySong]:
    """Explicit folder from a 'URL | folder' line: a path, or a folder name inside the library."""
    p = Path(spec).expanduser()
    if not p.is_absolute() and library_root is not None:
        p = library_root / spec
    p = p.resolve()
    hits = [s for s in library if s.path == p]
    if hits:
        return hits
    if p.is_dir():
        artist, name = _split_folder_name(p.name)
        vals = read_ini_values(p / "song.ini") if (p / "song.ini").exists() else {}
        has_video = any((p / f).exists() for f in VIDEO_FILES)
        return [LibrarySong(p, vals.get("artist", artist), vals.get("name", name), has_video)]
    raise FileNotFoundError(f"Song folder not found: {spec}")
