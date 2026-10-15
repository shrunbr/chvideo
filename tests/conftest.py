from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def synth_music(seconds: float, sr: int = 22050, seed: int = 0) -> np.ndarray:
    """Deterministic pseudo-music: plucked tones plus percussive noise bursts, so it has the
    kind of onsets the aligner relies on."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * sr), np.float32)
    notes = [110, 147, 196, 220, 262, 330, 392, 440, 523, 659, 784]
    t = 0.0
    while t < seconds - 0.5:
        n = int(rng.uniform(0.08, 0.4) * sr)
        tt = np.arange(n) / sr
        f = rng.choice(notes)
        tone = np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)
        if rng.random() < 0.4:
            tone = tone + rng.normal(0, 1, n) * np.exp(-tt * 30)
        i = int(t * sr)
        seg = (tone * np.exp(-tt * rng.uniform(4, 12))).astype(np.float32)[: len(out) - i]
        out[i : i + len(seg)] += seg
        t += rng.uniform(0.12, 0.35)
    return (out / np.abs(out).max() * 0.8).astype(np.float32)


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def make_video(path: Path, audio_wav: Path, seconds: float) -> None:
    """Tiny test video (moving test pattern) with the given soundtrack."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=s=160x90:r=25:d={seconds}", "-i", str(audio_wav),
         "-c:v", "mpeg4", "-q:v", "5", "-c:a", "pcm_s16le", "-shortest", str(path)],
        check=True,
    )


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never touch the real cache (or real env defaults) during tests."""
    monkeypatch.setenv("CHVIDEO_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CHVIDEO_LIBRARY", raising=False)
    monkeypatch.delenv("CHVIDEO_COOKIES", raising=False)
