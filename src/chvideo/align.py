"""Audio-to-audio alignment of a music video's soundtrack against the chart's audio.

Pipeline
--------
1. Both signals are turned into multi-band spectral-flux ("onset") envelopes at
   ~86 frames/s.  Onsets are robust to differences in mixing/mastering between the
   chart's stems and the video's soundtrack.
2. Coarse search: the song is cut into overlapping 8 s windows and every window is
   cross-correlated (normalised) against the *whole* video soundtrack.  This gives,
   for each window, a score for every possible offset.
3. A Viterbi pass picks one offset (or "no match") per window, penalising offset
   changes.  This yields piecewise-constant offsets, which handles music videos
   that add skits / extended intros or cut sections of the song.
4. Segment boundaries are refined frame-by-frame, then each segment's offset is
   refined to sub-millisecond precision with band-limited GCC-PHAT on the raw
   audio.  A linear fit across chunks also detects speed drift (e.g. PAL speed-up).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.fft import irfft, rfft
from scipy.signal import find_peaks
from scipy.stats import theilslopes

SR = 22050
HOP = 256
N_FFT = 2048
FRAME_RATE = SR / HOP  # ~86.13 feature frames per second

WIN_SEC = 8.0
WIN_HOP_SEC = 1.0
NULL_SCORE = 0.25  # emission score of the "this part of the song isn't in the video" state
SWITCH_PENALTY = 2.0  # Viterbi cost of changing offset
LOCAL_NULL = 0.15  # same idea, for frame-level boundary refinement
MIN_SEGMENT_SEC = 3.0
CONFIRMED_SCORE = 0.6  # onset-match score that counts as "found" even without a waveform match


class AlignError(RuntimeError):
    pass


@dataclass
class Segment:
    """Song interval [song_start, song_end) maps to video audio time
    video_start + (t - song_start) * speed."""

    song_start: float
    song_end: float
    video_start: float
    speed: float = 1.0
    confidence: float = 0.0  # median NCC of the coarse windows in this segment
    fine_chunks: int = 0  # how many GCC-PHAT chunks agreed (0 = coarse precision only)
    fine_spread_ms: float = float("nan")

    @property
    def offset(self) -> float:
        """video time - song time at the segment start (positive = video is 'later')."""
        return self.video_start - self.song_start

    def video_time(self, t: float) -> float:
        return self.video_start + (t - self.song_start) * self.speed


@dataclass
class AlignResult:
    segments: list[Segment]
    song_duration: float
    video_duration: float
    coverage: float  # fraction of (non-silent) song time matched to the video
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- features

def _band_matrix(n_bins: int, n_bands: int = 16, fmin: float = 60.0, fmax: float = 10000.0) -> np.ndarray:
    freqs = np.linspace(0, SR / 2, n_bins)
    edges = np.geomspace(fmin, fmax, n_bands + 1)
    m = np.zeros((n_bins, n_bands), dtype=np.float32)
    for b in range(n_bands):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not sel.any():  # guarantee every band has at least one bin
            sel[np.argmin(np.abs(freqs - (edges[b] + edges[b + 1]) / 2))] = True
        m[sel, b] = 1.0
    return m


def onset_features(x: np.ndarray) -> np.ndarray:
    """Multi-band spectral flux, shape (bands, frames), each band zero-mean/unit-std."""
    window = np.hanning(N_FFT).astype(np.float32)
    padded = np.concatenate([np.zeros(N_FFT // 2, np.float32), x, np.zeros(N_FFT, np.float32)])
    n_frames = 1 + (len(padded) - N_FFT) // HOP
    bands = _band_matrix(N_FFT // 2 + 1)
    frames = np.lib.stride_tricks.sliding_window_view(padded, N_FFT)[::HOP][:n_frames]
    energy = np.empty((n_frames, bands.shape[1]), dtype=np.float32)
    for i in range(0, n_frames, 4096):
        spec = np.abs(rfft(frames[i : i + 4096] * window, axis=1)).astype(np.float32) ** 2
        energy[i : i + 4096] = spec @ bands
    floor = 1e-3 * energy.mean(axis=0, keepdims=True) + 1e-12
    log_e = np.log(energy + floor)
    flux = np.maximum(0.0, np.diff(log_e, axis=0, prepend=log_e[:1]))
    # light smoothing: tolerate tiny timing differences between the two mixes
    k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    flux = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, flux)
    flux = flux.T
    flux -= flux.mean(axis=1, keepdims=True)
    flux /= flux.std(axis=1, keepdims=True) + 1e-9
    return flux.astype(np.float32)


# --------------------------------------------------------------------------- coarse search

def _window_scores(S: np.ndarray, V: np.ndarray, win: int, starts: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Normalised cross-correlation of each song window against every video position.

    Returns (scores[n_windows, n_positions], silent[n_windows]).
    """
    B, Nv = V.shape
    n_pos = Nv - win + 1
    if n_pos <= 0:
        raise AlignError("The video is shorter than the alignment window.")
    nfft = 1 << int(np.ceil(np.log2(Nv + win)))
    Vf = rfft(V, nfft, axis=1)
    csum = np.concatenate([np.zeros((B, 1)), np.cumsum(V, axis=1, dtype=np.float64)], axis=1)
    csq = np.concatenate([np.zeros((B, 1)), np.cumsum(V.astype(np.float64) ** 2, axis=1)], axis=1)
    vsum = csum[:, win:] - csum[:, :-win]
    vsq = csq[:, win:] - csq[:, :-win]
    v_den = np.sqrt(np.maximum((vsq - vsum**2 / win).sum(axis=0), 1e-9))[:n_pos]

    scores = np.zeros((len(starts), n_pos), dtype=np.float32)
    silent = np.zeros(len(starts), dtype=bool)
    for i, st in enumerate(starts):
        s = S[:, st : st + win].astype(np.float64)
        s = s - s.mean(axis=1, keepdims=True)
        s_norm = np.sqrt((s**2).sum())
        if s_norm < 1e-3 * np.sqrt(win * B):
            silent[i] = True
            continue
        num = irfft(np.conj(rfft(s, nfft, axis=1)) * Vf, nfft, axis=1).sum(axis=0)[:n_pos]
        scores[i] = num / (s_norm * v_den)
    return scores, silent


def _candidate_offsets(scores: np.ndarray, silent: np.ndarray, starts: list[int], per_window: int = 5) -> np.ndarray:
    picks: list[tuple[float, int]] = []
    for i, st in enumerate(starts):
        if silent[i]:
            continue
        row = scores[i]
        peaks, props = find_peaks(row, height=NULL_SCORE * 0.8, distance=int(FRAME_RATE * 0.5))
        if len(peaks) == 0:
            continue
        best = peaks[np.argsort(props["peak_heights"])[::-1][:per_window]]
        picks.extend((float(row[p]), int(p) - st) for p in best)
    picks.sort(reverse=True)
    seeds: list[int] = []
    for _, off in picks:
        if all(abs(off - s) > 3 for s in seeds):
            seeds.append(off)
    return np.array(sorted(seeds), dtype=np.int64)


def _viterbi(emis: np.ndarray, null_emis: np.ndarray) -> np.ndarray:
    """emis[w, s] for offset states, null_emis[w] for the no-match state.
    Returns the chosen state per window (-1 = null)."""
    W, S = emis.shape
    E = np.concatenate([emis, null_emis[:, None]], axis=1)  # null is state index S
    delta = E[0].copy()
    back = np.zeros((W, S + 1), dtype=np.int32)
    for w in range(1, W):
        best_prev = int(np.argmax(delta))
        best_val = delta[best_prev]
        # staying is free, switching costs SWITCH_PENALTY (half when entering/leaving null)
        stay = delta
        switch_val = np.full(S + 1, best_val - SWITCH_PENALTY)
        switch_val[S] = best_val - SWITCH_PENALTY / 2
        # leaving null is also cheaper
        from_null = delta[S] - SWITCH_PENALTY / 2
        better_null = from_null > switch_val
        switch_val = np.where(better_null, from_null, switch_val)
        switch_src = np.where(better_null, S, best_prev)
        use_stay = stay >= switch_val
        delta = np.where(use_stay, stay, switch_val) + E[w]
        back[w] = np.where(use_stay, np.arange(S + 1), switch_src)
    path = np.zeros(W, dtype=np.int64)
    path[-1] = int(np.argmax(delta))
    for w in range(W - 1, 0, -1):
        path[w - 1] = back[w, path[w]]
    return np.where(path == S, -1, path)


# --------------------------------------------------------------------------- boundary refinement

def _local_similarity(S: np.ndarray, V: np.ndarray, offset: int | None, half: int) -> np.ndarray:
    """Per-frame local Pearson correlation between song frames around t and video
    frames around t+offset (window of 2*half+1 frames)."""
    Ns, Nv = S.shape[1], V.shape[1]
    if offset is None:
        return np.full(Ns, LOCAL_NULL)
    idx = np.arange(Ns) + offset
    valid = (idx >= 0) & (idx < Nv)
    Vs = np.zeros_like(S, dtype=np.float64)
    Vs[:, valid] = V[:, idx[valid]]
    Sd = S.astype(np.float64)
    n = 2 * half + 1

    def movsum(a: np.ndarray) -> np.ndarray:  # centred moving sum along the last axis
        c = np.cumsum(np.pad(a, [(0, 0)] * (a.ndim - 1) + [(half + 1, half)]), axis=-1)
        return c[..., n:] - c[..., :-n]

    ms, mv = movsum(Sd), movsum(Vs)
    num = movsum((Sd * Vs).sum(axis=0)) - (ms * mv).sum(axis=0) / n
    ss = movsum((Sd * Sd).sum(axis=0)) - (ms * ms).sum(axis=0) / n
    vv = movsum((Vs * Vs).sum(axis=0)) - (mv * mv).sum(axis=0) / n
    sim = num / np.sqrt(np.maximum(ss, 0) * np.maximum(vv, 0) + 1e-9)
    sim[~valid] = -1.0
    # song silence carries no evidence either way
    pos = ss[ss > 0]
    quiet = ss < 0.05 * np.median(pos) if pos.size else np.ones(Ns, bool)
    sim[quiet & valid] = LOCAL_NULL
    return sim


def _best_split(sim_a: np.ndarray, sim_b: np.ndarray, lo: int, hi: int) -> int:
    """Index c in [lo, hi] maximising sum(sim_a[lo:c]) + sum(sim_b[c:hi])."""
    if hi <= lo:
        return lo
    a = np.concatenate([[0.0], np.cumsum(sim_a[lo:hi])])
    b = np.concatenate([[0.0], np.cumsum(sim_b[lo:hi])])
    total_b = b[-1]
    gain = a + (total_b - b)
    return lo + int(np.argmax(gain))


# --------------------------------------------------------------------------- fine refinement

def _gcc_phat(x: np.ndarray, y: np.ndarray, max_lag: int) -> tuple[float, float]:
    """Find lag L (0..2*max_lag) where y[L:L+len(x)] best matches x.
    Returns (lag_in_samples - max_lag, peak-to-noise ratio)."""
    n = len(x) + len(y)
    nfft = 1 << int(np.ceil(np.log2(n)))
    X = rfft(x, nfft)
    Y = rfft(y, nfft)
    R = Y * np.conj(X)
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    band = (freqs >= 100) & (freqs <= 8000)
    R = np.where(band, R / (np.abs(R) + 1e-12), 0)
    r = np.abs(irfft(R, nfft)[: 2 * max_lag + 1])
    k = int(np.argmax(r))
    frac = 0.0
    if 0 < k < len(r) - 1:
        a, b, c = r[k - 1], r[k], r[k + 1]
        denom = a - 2 * b + c
        if denom != 0:
            frac = 0.5 * (a - c) / denom
    mask = np.ones(len(r), bool)
    mask[max(0, k - 20) : k + 21] = False
    noise = r[mask].std() + 1e-12 if mask.any() else 1e-12
    return k + frac - max_lag, float((r[k] - r[mask].mean()) / noise) if mask.any() else 0.0


def _refine_segment(seg: Segment, song: np.ndarray, vid: np.ndarray) -> Segment:
    chunk = int(6.0 * SR)
    max_lag = int(0.06 * SR)
    dur = seg.song_end - seg.song_start
    usable = dur - 6.0
    if usable < 0:
        return seg
    n_chunks = int(min(24, max(1, usable // 4 + 1)))
    times, offs = [], []
    for c in range(n_chunks):
        t0 = seg.song_start + (usable * c / max(1, n_chunks - 1) if n_chunks > 1 else usable / 2)
        a = int(round(t0 * SR))
        x = song[a : a + chunk]
        if len(x) < chunk or np.sqrt(np.mean(x**2)) < 1e-3:
            continue
        v0 = int(round(seg.video_time(t0) * SR)) - max_lag
        if v0 < 0 or v0 + chunk + 2 * max_lag > len(vid):
            continue
        y = vid[v0 : v0 + chunk + 2 * max_lag]
        lag, pnr = _gcc_phat(x, y, max_lag)
        if pnr < 12.0:
            continue
        times.append(t0 + 3.0)
        offs.append(seg.video_time(t0) - t0 + lag / SR)
    if not offs:
        return seg
    times_a, offs_a = np.array(times), np.array(offs)
    if len(offs_a) >= 4 and times_a.max() - times_a.min() >= 20.0:
        # robust line fit first so genuine drift isn't thrown away as "outliers"
        slope, icpt, _, _ = theilslopes(offs_a, times_a)
        keep = np.abs(offs_a - (slope * times_a + icpt)) < 0.006
        if keep.sum() >= 3:
            slope, icpt = np.polyfit(times_a[keep], offs_a[keep], 1)
        resid = offs_a[keep] - (slope * times_a[keep] + icpt)
        if abs(slope) * dur > 0.015 and abs(slope) < 0.08:
            v_start = seg.song_start * (1 + slope) + icpt
            return Segment(seg.song_start, seg.song_end, v_start, 1.0 + slope, seg.confidence,
                           int(keep.sum()), float(resid.std() * 1000))
    else:
        keep = np.abs(offs_a - np.median(offs_a)) < 0.008
    if not keep.any():
        return seg
    # constant offset: midpoint of the observed range minimises the worst-case error
    off = float((offs_a[keep].min() + offs_a[keep].max()) / 2)
    spread = float(np.std(offs_a[keep]) * 1000)
    return Segment(seg.song_start, seg.song_end, seg.song_start + off, 1.0, seg.confidence,
                   int(keep.sum()), spread)


# --------------------------------------------------------------------------- main entry

def align(song: np.ndarray, vid: np.ndarray, log=print) -> AlignResult:
    song_dur, vid_dur = len(song) / SR, len(vid) / SR
    log(f"  computing onset features (song {song_dur:.1f}s, video {vid_dur:.1f}s)")
    S, V = onset_features(song), onset_features(vid)
    win = int(round(WIN_SEC * FRAME_RATE))
    hop = int(round(WIN_HOP_SEC * FRAME_RATE))
    if S.shape[1] < win:
        raise AlignError("Song is too short to align.")
    starts = list(range(0, S.shape[1] - win + 1, hop))
    if starts[-1] != S.shape[1] - win:
        starts.append(S.shape[1] - win)

    log(f"  matching {len(starts)} song windows against the video")
    scores, silent = _window_scores(S, V, win, starts)
    cands = _candidate_offsets(scores, silent, starts)
    if len(cands) == 0:
        raise AlignError("No part of the song could be found in the video's audio.")

    n_pos = scores.shape[1]
    emis = np.full((len(starts), len(cands)), -0.2, dtype=np.float32)
    for i, st in enumerate(starts):
        if silent[i]:
            emis[i] = 0.0
            continue
        pos = st + cands
        for d in (-2, -1, 0, 1, 2):
            p = pos + d
            ok = (p >= 0) & (p < n_pos)
            emis[i, ok] = np.maximum(emis[i, ok], scores[i, p[ok]])
    null_emis = np.where(silent, 0.0, NULL_SCORE).astype(np.float32)
    path = _viterbi(emis, null_emis)

    # runs of identical states -> (state, first_window, last_window)
    runs: list[list[int]] = []
    for w, s in enumerate(path):
        if runs and runs[-1][0] == s:
            runs[-1][2] = w
        else:
            runs.append([int(s), w, w])

    # frame-level boundaries between consecutive runs
    half = int(round(0.4 * FRAME_RATE))
    sims: dict[int, np.ndarray] = {}

    def sim_for(state: int) -> np.ndarray:
        if state not in sims:
            sims[state] = _local_similarity(S, V, None if state < 0 else int(cands[state]), half)
        return sims[state]

    bounds = [0]
    for (sa, _, a_last), (sb, b_first, _) in zip(runs, runs[1:]):
        lo = max(bounds[-1], starts[a_last])
        hi = max(lo, min(S.shape[1], starts[b_first] + win))
        bounds.append(_best_split(sim_for(sa), sim_for(sb), lo, hi))
    bounds.append(S.shape[1])

    segments: list[Segment] = []
    for (state, w0, w1), f0, f1 in zip(runs, bounds, bounds[1:]):
        if state < 0:
            continue
        t0, t1 = f0 / FRAME_RATE, min(f1 / FRAME_RATE, song_dur)
        if t1 - t0 < MIN_SEGMENT_SEC:
            continue
        off = cands[state] / FRAME_RATE
        conf = float(np.median(emis[w0 : w1 + 1, state]))
        segments.append(Segment(t0, t1, t0 + off, 1.0, conf))

    if not segments:
        raise AlignError("The song's audio could not be matched anywhere in the video.")

    log(f"  refining {len(segments)} segment(s) with GCC-PHAT")
    segments = [_refine_segment(s, song, vid) for s in segments]
    segments = _merge_compatible(segments, song, vid)

    # Coverage over non-silent song time.  Only segments confirmed by the waveform check
    # (or with a strong onset match) count: a different recording of the same song
    # (live version, re-record) produces loose onset matches that never confirm.
    loud = _loud_mask(song)
    matched = np.zeros_like(loud)
    step = 0.1
    for s in segments:
        if s.fine_chunks > 0 or s.confidence >= CONFIRMED_SCORE:
            matched[int(s.song_start / step) : int(s.song_end / step)] = True
    coverage = float((matched & loud).sum() / max(1, loud.sum()))

    notes = []
    if any(s.fine_chunks == 0 for s in segments):
        notes.append("Some segments could only be aligned at ~10 ms precision (no sharp waveform match).")
    if any(s.speed != 1.0 for s in segments):
        notes.append("The video plays at a slightly different speed than the song; compensated.")
    return AlignResult(segments, song_dur, vid_dur, coverage, notes)


def _loud_mask(x: np.ndarray, step: float = 0.1) -> np.ndarray:
    n = int(len(x) / (SR * step))
    if n == 0:
        return np.zeros(0, bool)
    rms = np.sqrt(np.mean(x[: int(n * SR * step)].reshape(n, -1) ** 2, axis=1))
    return rms > 0.02 * np.percentile(rms, 95)


def _merge_compatible(segments: list[Segment], song: np.ndarray, vid: np.ndarray) -> list[Segment]:
    """Merge adjacent segments that are really one continuous mapping (happens when
    the coarse search split a drifting section into several fixed offsets)."""
    out = [segments[0]]
    for seg in segments[1:]:
        prev = out[-1]
        gap = seg.song_start - prev.song_end
        if gap < 1.0 and abs(prev.video_time(seg.song_start) - seg.video_start) < 0.025:
            merged = Segment(prev.song_start, seg.song_end, prev.video_start, 1.0,
                             min(prev.confidence, seg.confidence))
            out[-1] = _refine_segment(merged, song, vid)
        else:
            out.append(seg)
    return out


def manual_alignment(song_duration: float, video_duration: float, offset: float) -> AlignResult:
    """Single fixed offset: video time = song time + offset."""
    return AlignResult([Segment(0.0, song_duration, offset, 1.0, 1.0)], song_duration, video_duration, 1.0,
                       ["Manual offset - no audio analysis performed."])
