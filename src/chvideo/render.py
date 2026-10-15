"""Turn an alignment into a video whose frame at time t belongs to song time t."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .align import AlignResult
from .media import MediaError, VideoInfo


@dataclass
class Piece:
    """Song interval [t0, t1) shows video (container-relative) time v0 + (t - t0) * speed,
    or black when v0 is None."""

    t0: float
    t1: float
    v0: float | None
    speed: float = 1.0
    kind: str = "sync"  # sync | fill | black


def build_timeline(result: AlignResult, video_duration: float, gap_fill: str = "continue") -> list[Piece]:
    """Cover the whole song with pieces.  Gaps (song parts the video doesn't contain)
    are filled by letting the neighbouring footage keep playing, or with black."""
    song_dur = result.song_duration
    segs = sorted(result.segments, key=lambda s: s.song_start)
    pieces: list[Piece] = []

    def video_end_song_time(p: Piece) -> float:
        return p.t0 + (video_duration - p.v0) / p.speed

    cursor = 0.0
    for seg in segs:
        start = max(seg.song_start, cursor)
        if start >= seg.song_end:
            continue
        nxt = Piece(start, seg.song_end, seg.video_time(start), seg.speed)
        if start > cursor + 1e-6:
            gap_t0, gap_t1 = cursor, start
            if gap_fill == "continue" and pieces and pieces[-1].v0 is not None:
                prev = pieces[-1]
                reach = min(gap_t1, video_end_song_time(prev))
                if reach > gap_t0:
                    pieces.append(Piece(gap_t0, reach, prev.v0 + (gap_t0 - prev.t0) * prev.speed, prev.speed, "fill"))
                    gap_t0 = reach
            if gap_fill == "continue" and gap_t0 < gap_t1:
                # play the upcoming footage early, as far back as the video goes
                earliest = max(gap_t0, nxt.t0 - nxt.v0 / nxt.speed)
                if earliest < gap_t1:
                    pieces.append(Piece(earliest, gap_t1, nxt.v0 - (gap_t1 - earliest) * nxt.speed, nxt.speed, "fill"))
                    gap_t1 = earliest
            if gap_t0 < gap_t1:
                pieces.append(Piece(gap_t0, gap_t1, None, 1.0, "black"))
        pieces.append(nxt)
        cursor = seg.song_end

    if cursor < song_dur:
        if gap_fill == "continue" and pieces and pieces[-1].v0 is not None:
            prev = pieces[-1]
            reach = min(song_dur, video_end_song_time(prev))
            if reach > cursor:
                pieces.append(Piece(cursor, reach, prev.v0 + (cursor - prev.t0) * prev.speed, prev.speed, "fill"))
                cursor = reach
        if cursor < song_dur:
            pieces.append(Piece(cursor, song_dur, None, 1.0, "black"))

    pieces.sort(key=lambda p: p.t0)
    # merge pieces that are one continuous mapping (fill extended from its neighbour)
    merged: list[Piece] = []
    for p in pieces:
        if merged:
            q = merged[-1]
            same_black = q.v0 is None and p.v0 is None
            continuous = (
                q.v0 is not None and p.v0 is not None and abs(q.speed - p.speed) < 1e-9
                and abs(q.v0 + (p.t0 - q.t0) * q.speed - p.v0) < 1e-4
            )
            if abs(q.t1 - p.t0) < 1e-6 and (same_black or continuous):
                q.t1 = p.t1
                if q.kind != p.kind:
                    q.kind = "sync+fill"
                continue
        merged.append(Piece(p.t0, p.t1, p.v0, p.speed, p.kind))
    return merged


@dataclass
class EncodeSettings:
    container: str = "webm"  # webm (VP8, works on every platform) or mp4 (H.264)
    max_height: int = 1080
    fps: Fraction | None = None
    quality: int = 10  # VP8 crf (4-63) / x264 crf (lower = better)
    jobs: int | None = None  # parallel encoder processes (default: all cores)
    encoder: str = "cpu"  # cpu (libvpx / libx264) or nvenc (NVIDIA GPU, H.264 mp4 only)


# NVENC sessions per process are cheap, but GeForce drivers cap concurrent sessions
# (8 on current drivers, 3-5 on older ones); decoding/filtering still runs on the CPU, so a
# few parallel chunks keep the GPU fed.
NVENC_MAX_JOBS = 4


@functools.cache
def nvenc_status() -> str | None:
    """None if NVENC H.264 encoding works on this machine, else the reason it doesn't."""
    probe = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.5",
         "-c:v", "h264_nvenc", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if probe.returncode == 0:
        return None
    lines = probe.stderr.decode(errors="replace").strip().splitlines()
    hint = next((ln for ln in lines if "nvenc" in ln.lower() or "cuda" in ln.lower()), lines[-1] if lines else "")
    return hint.strip() or "h264_nvenc is not available"


def output_geometry(info: VideoInfo, settings: EncodeSettings) -> tuple[int, int, Fraction]:
    h = min(info.height, settings.max_height)
    h -= h % 2
    w = int(round(info.width * h / info.height / 2)) * 2
    if settings.fps:
        fps = settings.fps
    else:
        fps = info.fps
        if fps > 60 or fps <= 0:
            fps = Fraction(30)
    return w, h, fps


@dataclass
class FramePiece:
    """Output frames [k0, k1) show source (container-relative) time src + (k - k0) / fps * speed,
    or black when src is None."""

    k0: int
    k1: int
    src: float | None
    speed: float = 1.0

    def clip(self, a: int, b: int, fps: Fraction) -> FramePiece | None:
        k0, k1 = max(self.k0, a), min(self.k1, b)
        if k1 <= k0:
            return None
        src = None if self.src is None else self.src + float((k0 - self.k0) / fps) * self.speed
        return FramePiece(k0, k1, src, self.speed)


def to_frame_pieces(pieces: list[Piece], info: VideoInfo, fps: Fraction, total_frames: int) -> list[FramePiece]:
    out = []
    for p in pieces:
        k0, k1 = int(round(p.t0 * fps)), min(int(round(p.t1 * fps)), total_frames)
        if k1 <= k0:
            continue
        # source time on screen at output frame k0, container-relative (what -ss uses)
        src = None if p.v0 is None else p.v0 + (float(k0 / fps) - p.t0) * p.speed + info.audio_shift
        out.append(FramePiece(k0, k1, src, p.speed))
    return out


def default_jobs() -> int:
    """One encoder per physical core: on SMT CPUs the second hardware thread adds
    contention but no throughput for libvpx (measured on a 12c/24t Ryzen)."""
    try:
        import psutil

        cores = psutil.cpu_count(logical=False)
    except Exception:
        cores = None
    return max(1, cores or os.cpu_count() or 4)


def _codec_args(settings: EncodeSettings, w: int, h: int, fps: Fraction, threads: int) -> list[str]:
    gop = str(int(round(float(fps) * 2)))  # keyframe every 2s: fast seeking in practice mode
    # ~4 Mbit/s at 1080p (scaled by pixel count): ~120 MB for a 4 minute song
    max_kbps = max(1000, int(4000 * w * h / (1920 * 1080)))
    if settings.encoder == "nvenc":
        # Same bitrate budget as VP8 but far better quality (SSIM 0.988 vs 0.914 measured).
        # No B-frames: they gained nothing here and keep the chunk joins trivially exact.
        return ["-c:v", "h264_nvenc", "-preset", "p3", "-tune", "hq", "-rc", "vbr",
                "-cq", str(settings.quality + 18), "-b:v", "0", "-maxrate", f"{max_kbps}k",
                "-bufsize", f"{2 * max_kbps}k", "-bf", "0", "-spatial-aq", "1", "-g", gop,
                "-pix_fmt", "yuv420p"]
    if settings.container == "mp4":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(settings.quality + 8),
                "-pix_fmt", "yuv420p", "-g", gop, "-threads", str(threads)]
    return ["-c:v", "libvpx", "-deadline", "good", "-cpu-used", "6", "-crf", str(settings.quality),
            "-b:v", f"{max_kbps}k", "-qmin", "0", "-qmax", "50", "-g", gop, "-threads", str(threads),
            "-auto-alt-ref", "0"]


def _chunk_command(
    fpieces: list[FramePiece], info: VideoInfo, w: int, h: int, fps: Fraction, codec: list[str], out: Path
) -> tuple[list[str], int]:
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    chains, labels = [], []
    for i, p in enumerate(fpieces):
        frames = p.k1 - p.k0
        if p.src is None:
            cmd += ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}"]
            chain = f"[{i}:v]trim=end_frame={frames},setsar=1,format=yuv420p,setpts=PTS-STARTPTS"
        else:
            # Seek a little early, then cut exactly in the filter graph so output frame 0
            # shows the source frame on screen at time p.src.
            seek = max(0.0, p.src - 2.0)
            pre = p.src - seek
            cmd += ["-threads", "2", "-ss", f"{seek:.6f}", "-i", str(info.path)]
            chain = (
                f"[{i}:v:0]setpts=(PTS-{pre:.6f}/TB)/{p.speed:.8f},"
                f"fps={fps}:start_time=0,"
                f"tpad=stop_mode=clone:stop_duration=5,"
                f"trim=end_frame={frames},"
                f"scale={w}:{h}:flags=lanczos,setsar=1,format=yuv420p,setpts=PTS-STARTPTS"
            )
        labels.append(f"[p{i}]")
        chains.append(chain + labels[-1])
    if len(labels) == 1:
        graph = chains[0].replace(labels[0], "[out]")
    else:
        graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[out]"
    frames = sum(p.k1 - p.k0 for p in fpieces)
    cmd += ["-filter_complex", graph, "-map", "[out]", "-an", "-sn", "-dn", "-r", str(fps),
            "-frames:v", str(frames), *codec, "-progress", "pipe:1", "-nostats", str(out)]
    return cmd, frames


def render(
    pieces: list[Piece], info: VideoInfo, out_path: Path, settings: EncodeSettings, song_duration: float
) -> None:
    """Encode the timeline.  libvpx's VP8 encoder barely scales past ~2 threads, so the
    timeline is split into chunks encoded by parallel single-threaded ffmpeg processes and
    then joined losslessly (each chunk starts on a keyframe)."""
    w, h, fps = output_geometry(info, settings)
    total_frames = int(round(song_duration * fps))
    fpieces = to_frame_pieces(pieces, info, fps, total_frames)

    if settings.encoder == "nvenc":
        workers = min(settings.jobs or NVENC_MAX_JOBS, 8)
    else:
        workers = settings.jobs or default_jobs()
    min_chunk = int(float(fps) * 6)
    n_chunks = max(1, min(workers, total_frames // max(1, min_chunk)))
    bounds = [round(total_frames * i / n_chunks) for i in range(n_chunks + 1)]
    codec = _codec_args(settings, w, h, fps, threads=1 if n_chunks > 1 else min(workers, 16))
    ext = "mp4" if settings.container == "mp4" else "webm"

    with tempfile.TemporaryDirectory(prefix="chvideo-", dir=out_path.parent, ignore_cleanup_errors=True) as td:
        jobs = []
        for c in range(n_chunks):
            chunk = [q for p in fpieces if (q := p.clip(bounds[c], bounds[c + 1], fps))]
            part = Path(td) / f"part{c:03d}.{ext}"
            cmd, frames = _chunk_command(chunk, info, w, h, fps, codec, part)
            jobs.append((cmd, frames, part))

        where = (f"GPU (NVENC x{n_chunks})" if settings.encoder == "nvenc"
                 else f"{n_chunks} CPU core{'s' if n_chunks > 1 else ''}")
        label = f"encoding {w}x{h} @ {float(fps):.3f} fps on {where}"
        progress = [0] * n_chunks
        lock = threading.Lock()
        stop = threading.Event()

        def report() -> None:
            while not stop.wait(0.5):
                with lock:
                    pct = 100 * sum(progress) / max(1, total_frames)
                sys.stdout.write(f"\r  {label}: {pct:5.1f}%")
                sys.stdout.flush()

        reporter = threading.Thread(target=report, daemon=True)
        reporter.start()
        try:
            with ThreadPoolExecutor(max_workers=n_chunks) as pool:
                futures = [pool.submit(_run_ffmpeg, cmd, i, progress, lock) for i, (cmd, _, _) in enumerate(jobs)]
                errors = [e for f in futures if (e := f.exception())]
        finally:
            stop.set()
            reporter.join()
        sys.stdout.write(f"\r  {label}: 100.0%\n" if not errors else "\n")
        if errors:
            raise errors[0]

        if n_chunks == 1:
            shutil.move(jobs[0][2], out_path)
            return
        listing = Path(td) / "parts.txt"
        lines = ["ffconcat version 1.0"]
        for _, frames, part in jobs:
            # explicit durations keep the join frame-exact (no container rounding)
            lines += [f"file '{part.name}'", f"duration {float(Fraction(frames) / fps):.9f}"]
        listing.write_text("\n".join(lines) + "\n")
        join = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy"]
        if ext == "mp4":
            join += ["-movflags", "+faststart"]
        proc = subprocess.run(join + [str(out_path)], stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise MediaError("ffmpeg failed while joining chunks:\n" + proc.stderr.decode(errors="replace")[-2000:])


def _run_ffmpeg(cmd: list[str], idx: int, progress: list[int], lock: threading.Lock) -> None:
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("frame="):
                with lock:
                    progress[idx] = int(line.split("=", 1)[1].strip() or 0)
        proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode(errors="replace").strip().splitlines()[-20:]
            raise MediaError("ffmpeg failed while rendering:\n" + "\n".join(tail))


def make_preview(video: Path, stems: list[Path], out_path: Path) -> None:
    """Mux the rendered video with the chart's own audio so the sync can be checked
    in any media player before launching the game."""
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video)]
    for s in stems:
        cmd += ["-i", str(s)]
    n = len(stems)
    amix = "".join(f"[{i + 1}:a]" for i in range(n)) + (
        f"amix=inputs={n}:normalize=0:duration=longest[a]" if n > 1 else "anull[a]"
    )
    cmd += ["-filter_complex", amix, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "libopus", "-b:a", "160k", str(out_path)]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise MediaError("ffmpeg failed while making preview:\n" + proc.stderr.decode(errors="replace")[-2000:])
