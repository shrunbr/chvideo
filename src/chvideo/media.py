"""Thin wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from .osutil import ffmpeg_install_hint

VIDEO_CONTAINER_EXTS = (".mkv", ".mp4", ".webm", ".mov", ".avi", ".flv")


class MediaError(RuntimeError):
    pass


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(f"'{tool}' was not found on PATH. {ffmpeg_install_hint()}")


def run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE if capture else subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-15:]
        raise MediaError(f"Command failed: {' '.join(cmd[:3])} ...\n" + "\n".join(tail))
    return proc


def probe(path: Path) -> dict:
    proc = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture=True,
    )
    return json.loads(proc.stdout)


def _rate(s: str | None) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    num, _, den = s.partition("/")
    return float(num) / float(den or 1)


STANDARD_FPS = (Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                Fraction(48), Fraction(50), Fraction(60000, 1001), Fraction(60))


def snap_fps(fps: float) -> Fraction:
    """Containers with millisecond timestamps (mkv/webm) report rates like 8273/345 for
    23.976 fps; snap to the standard rate so output frames line up with source frames."""
    for std in STANDARD_FPS:
        if abs(fps / float(std) - 1) < 0.002:
            return std
    return Fraction(fps).limit_denominator(1001)


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: Fraction
    duration: float
    # Seconds between the container's start time (what `ffmpeg -ss` is relative to)
    # and the first decoded audio sample.  Needed to map audio time -> video time.
    audio_shift: float
    has_audio: bool


def video_info(path: Path) -> VideoInfo:
    info = probe(path)
    streams = info.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    if not vstreams:
        raise MediaError(f"No video stream found in {path}")
    v = vstreams[0]
    fmt = info.get("format", {})
    fmt_start = float(fmt.get("start_time", 0) or 0)
    a_start = float(astreams[0].get("start_time", fmt_start) or fmt_start) if astreams else fmt_start
    fps = snap_fps(_rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")) or 30.0)
    duration = float(fmt.get("duration") or v.get("duration") or 0)
    width, height = int(v["width"]), int(v["height"])
    # Respect rotation / non-square pixels so the output has the right shape.
    sar = v.get("sample_aspect_ratio", "1:1")
    if sar and ":" in sar and sar not in ("0:1", "1:1"):
        n, d = (int(x) for x in sar.split(":"))
        if n and d:
            width = int(round(width * n / d))
    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        audio_shift=a_start - fmt_start,
        has_audio=bool(astreams),
    )


def media_duration(path: Path) -> float:
    info = probe(path)
    return float(info.get("format", {}).get("duration") or 0)


def decode_audio(path: Path, sr: int) -> np.ndarray:
    """Decode the first audio stream of `path` to mono float32 at `sr` Hz.

    Sample 0 is the first sample the decoder produces (encoder delay / pre-skip
    already removed), which is also how Clone Hero's audio engine plays stems.
    """
    proc = run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
        ],
        capture=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def mix_audio(paths: list[Path], sr: int) -> np.ndarray:
    """Decode and sum several stems (Clone Hero starts every stem at t=0)."""
    tracks = [decode_audio(p, sr) for p in paths]
    n = max(len(t) for t in tracks)
    out = np.zeros(n, dtype=np.float32)
    for t in tracks:
        out[: len(t)] += t
    peak = float(np.max(np.abs(out))) or 1.0
    return out / peak
