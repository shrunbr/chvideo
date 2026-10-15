"""Scan a Clone Hero library and match music-video titles to songs."""

from __future__ import annotations

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


@dataclass
class LibrarySong:
    path: Path
    artist: str
    name: str
    has_video: bool

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


def _split_folder_name(name: str) -> tuple[str, str]:
    """'Artist - Song (Charter)' -> ('Artist', 'Song'); used only when song.ini lacks tags."""
    name = re.sub(r"\s*[(\[][^)\]]*[)\]]\s*$", "", name)
    artist, sep, title = name.partition(" - ")
    return (artist, title) if sep else ("", name)


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
            songs.append(LibrarySong(Path(d), artist, name, bool(files.keys() & VIDEO_FILES)))
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
