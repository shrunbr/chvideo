"""Clone Hero song-folder helpers: locating audio stems and editing song.ini."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = (".opus", ".ogg", ".mp3", ".wav", ".flac", ".oga")
# Stems Clone Hero plays together.  "crowd" is excluded on purpose: crowd noise
# is never part of a music video's soundtrack and only hurts alignment.
STEM_NAMES = (
    "song", "guitar", "bass", "rhythm", "keys", "vocals", "vocals_1", "vocals_2",
    "drums", "drums_1", "drums_2", "drums_3", "drums_4",
)
VIDEO_EXTS = (".webm", ".mp4", ".avi", ".ogv", ".mpeg", ".mpg", ".mkv", ".mov")


class SongError(RuntimeError):
    pass


@dataclass
class SongFolder:
    path: Path
    stems: list[Path]
    ini: Path | None

    @property
    def title(self) -> str:
        if self.ini:
            vals = read_ini_values(self.ini)
            artist, name = vals.get("artist"), vals.get("name")
            if artist and name:
                return f"{artist} - {name}"
        return self.path.name

    def existing_videos(self) -> list[Path]:
        return sorted(
            p for p in self.path.iterdir()
            if p.is_file() and p.stem.lower() == "video" and p.suffix.lower() in VIDEO_EXTS
        )


def load_song_folder(path: Path) -> SongFolder:
    path = path.expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".sng":
        raise SongError(".sng packages are not supported; point at an extracted song folder instead.")
    if not path.is_dir():
        raise SongError(f"Song folder not found: {path}")
    files = {p.name.lower(): p for p in path.iterdir() if p.is_file()}
    stems = []
    for name in STEM_NAMES:
        for ext in AUDIO_EXTS:
            if (p := files.get(name + ext)) is not None:
                stems.append(p)
                break
    if not stems:
        raise SongError(f"No Clone Hero audio stems (song.ogg / guitar.opus / ...) found in {path}")
    return SongFolder(path=path, stems=stems, ini=files.get("song.ini"))


# --------------------------------------------------------------------------- song.ini

def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            return text, ("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else enc)
        except UnicodeDecodeError:
            continue
    raise SongError(f"Could not decode {path}")


def read_ini_values(path: Path) -> dict[str, str]:
    text, _ = _read_text(path)
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([^=\[;#]+?)\s*=\s*(.*?)\s*$", line)
        if m:
            out.setdefault(m.group(1).lower(), m.group(2))
    return out


def set_ini_value(path: Path, key: str, value: str) -> None:
    """Set `key = value` in the [song] section, preserving everything else."""
    text, enc = _read_text(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=", re.IGNORECASE)

    section_start = next((i for i, line in enumerate(lines) if line.strip().lower() == "[song]"), None)
    if section_start is None:
        lines = ["[Song]"] + lines
        section_start = 0
    section_end = next(
        (i for i in range(section_start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines)
    )

    replaced = False
    for i in range(section_start + 1, section_end):
        if key_re.match(lines[i]):
            lines[i] = f"{key} = {value}"
            replaced = True
    if not replaced:
        insert_at = section_end
        while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"{key} = {value}")

    backup = path.with_name(path.name + ".chvideo.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(newline.join(lines) + newline, encoding=enc, newline="")


def write_minimal_ini(path: Path, key: str, value: str) -> None:
    path.write_text(f"[Song]\n{key} = {value}\n", encoding="utf-8")
