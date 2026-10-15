# How it works

← [Back to README](../README.md)

- [The goal](#the-goal)
- [1. Download](#1-download)
- [2. Align](#2-align)
- [3. Render](#3-render)
- [4. Install](#4-install)
- [Batch matching](#batch-matching)
- [Accuracy](#accuracy)
- [Performance](#performance)

## The goal

Clone Hero plays a song's `video.*` file in the background and starts it together with the song
audio. The `video_start_time` tag in `song.ini` shifts it, in milliseconds: positive values skip
further into the video, negative values delay it. Music videos rarely line up with the chart
audio by themselves. They have intros, skits, cut verses, or simply start at a different moment.

chvideo measures the exact relationship between the two soundtracks. It then produces a new
video in which **frame *t* belongs to song time *t***, so it plays in sync with
`video_start_time = 0`.

## 1. Download

[yt-dlp](https://github.com/yt-dlp/yt-dlp) fetches the best video stream up to `--max-height`,
plus the best audio stream. The audio is only used for alignment; the picture is re-encoded
anyway. YouTube currently requires a JavaScript runtime to download; chvideo installs
[Deno](https://deno.com/) as a Python dependency and points yt-dlp at it.

## 2. Align

The chart's audio stems are decoded and mixed, exactly as Clone Hero plays them: every stem
starts at t = 0. The `crowd` stem is left out, since crowd noise is never in a music video. The
video's audio track is decoded the same way. Its offset against the video's picture is taken
from the container timestamps, so audio time can be mapped to frame time exactly.

**Features.** Both signals become 16-band *spectral flux* envelopes, about 86 frames per second.
These measure how much energy each frequency band gains from one moment to the next, which
captures the onsets of notes and drum hits. Onsets survive differences in mixing, mastering and
lossy compression between the chart's stems and the video's soundtrack.

**Coarse search.** The song is cut into overlapping 8-second windows, one per second. Each
window is cross-correlated (normalised) against the *entire* video soundtrack with FFTs. This
scores every possible offset for every window.

**Viterbi.** A dynamic-programming pass picks one offset per window, plus a "not in the video"
state. Changing offset costs a penalty, so the result is piecewise constant: one offset per
continuous section. This is what handles edited music videos:

- An inserted skit makes the offset jump forward.
- A cut verse makes the song have time the video lacks.
- Repeated choruses don't cause false jumps, because staying on the same offset is cheaper.

**Boundaries.** Where the offset changes, the cut point is refined frame by frame using local
correlation on each side.

**Fine refinement.** Each section is checked in several 6-second chunks with band-limited
**GCC-PHAT**, a phase-correlation method, on the raw audio. This gives the offset to sub-sample
precision. A robust (Theil–Sen) line fit across the chunks detects slow speed drift, for example
a slightly different sample rate, and compensates for it.

**Coverage and rejection.** "Coverage" is the share of the song's non-silent audio that was
found in the video. It only counts sections confirmed by the waveform check or by a strong onset
match. A live recording of the same song produces loose onset matches but never confirms. In
testing it scored 0% where real matches score 94–100%. Below `--min-coverage` (default 50%),
chvideo refuses to sync.

## 3. Render

The aligned sections become a timeline that covers the whole song:

- **Synced sections** map song time to video time, with a speed factor if drift was found.
- **Gaps:**
  - Song audio the video doesn't contain is filled by letting the neighbouring footage keep
    playing, or with black if you pass `--gap-fill black`.
  - Time before the video starts or after it ends is black.

The timeline is quantised to output frames (the source frame rate, snapped to standard rates
such as 23.976 or 29.97) and rendered with FFmpeg. Each section is seeked, re-timed and trimmed
exactly in the filter graph, then the sections are concatenated. There's a keyframe every 2
seconds, so Clone Hero's practice mode can seek quickly. The output has no audio track.

**Parallel chunks.** The timeline is split into chunks that are encoded at the same time by
separate FFmpeg processes, then joined without re-encoding. Each chunk starts on a keyframe, and
the join uses exact frame durations, so it is frame-exact.

**Formats:**

- **CPU (default): VP8 in `.webm`**. The [Clone Hero wiki](https://wiki.clonehero.net/books/clone-hero-manual/page/other-custom-content)
  lists it as the only video format that works on every platform.
- **`--gpu`: H.264 in `.mp4`**, encoded by NVIDIA NVENC, which works on Windows and macOS.
  GPUs can't encode VP8. chvideo probes NVENC with a tiny test encode first, and falls back to
  the CPU if NVENC is unavailable or fails partway through a song.

## 4. Install

- **Video.** The result is copied into the song folder as `video.webm` (or `video.mp4`). An
  existing `video.*` is only replaced with `--force`, and is renamed to `video.<ext>.bak`.
- **`song.ini`.** `video_start_time` is set to 0, or to `--nudge-ms`. Every other line, the
  encoding and the line endings are preserved, and the original is backed up once as
  `song.ini.chvideo.bak`.

## Batch matching

Each video's title, channel name and YouTube's own artist/track tags are compared with the
`artist` and `name` in every `song.ini`, using [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz).
The comparison is tuned for typical titles:

- Bracketed noise like "(Official Music Video)" or "[HD]" is ignored.
- Channel names like "ArtistVEVO" count as the artist.
- Chart annotations like "(Live)" or "(X cover)" match slightly less well than an exact title.

Title matching only proposes candidates. The [audio alignment](#2-align) decides, so a video is
never installed into a song whose audio it doesn't contain.

## Accuracy

These checks were run during development:

- **Against hand-synced charts.** Charts in a real library that already shipped videos with
  hand-tuned `video_start_time` values were re-aligned. The computed offsets matched the
  charters' values within normal by-ear tuning (for example −2003 ms vs −2000, −5313 vs −5350,
  120189 vs 120500). This also confirms the sign convention and that Clone Hero adds no hidden
  offset.
- **Synthetic edits.** A soundtrack was built from a song with a fake intro, a cut 15-second
  section and an inserted 5-second skit, then sent through a lossy codec. All three sections were
  recovered at their exact offsets, with cut points within 0.3 s.
- **Frame checks.** Rendered videos were compared frame by frame against the original download
  across the whole song, through every chunk join. The error stayed constant at about 13 ms with
  no drift. That's under half a frame: the unavoidable rounding to the nearest video frame.

## Performance

Measured on a Ryzen 9 5900X (12 cores / 24 threads) and an RTX 3080, for a 4-minute 1080p song,
excluding download and alignment:

| Encoder | Time | CPU time | Quality (SSIM) |
| --- | --- | --- | --- |
| VP8, one FFmpeg process, 16 threads (first version) | ~72 s | | 0.914 |
| VP8, 12 parallel single-threaded chunks (default) | ~23 s | ~270 s | 0.914 |
| H.264 NVENC, `--gpu` | ~16 s | ~46 s | 0.988 |

These numbers explain the defaults:

- **Parallel chunks.** libvpx's VP8 encoder barely scales past two threads: with 16 threads,
  most of its CPU time was spent waiting. One single-threaded encoder per chunk scales almost
  linearly.
- **One job per physical core.** Hyperthreads added contention without throughput: 12 jobs
  took 33 s, 24 jobs 36 s.
- **VP8 preset.** `-cpu-used 6` encodes 30% faster than 4, with identical size and quality.
- **NVENC preset.** NVENC is limited by the GPU's single encoder engine, so more GPU sessions
  don't help. Preset `p3` is 1.85× faster than `p5` at practically the same quality.
- **Same file size.** Both encoders target about 4 Mbit/s at 1080p (around 115–120 MB per
  4-minute song). At that budget H.264 is noticeably sharper than VP8.
