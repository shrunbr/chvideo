# Troubleshooting

← [Back to README](../README.md)

## Errors from chvideo

### `This song already has video.mp4. Use --force to replace it`

chvideo never overwrites a video unless you ask. Add `--force`; the old file is renamed to
`video.mp4.bak` (or `video.webm.bak`). In batch mode, songs that already have a video are
skipped. `--force` includes them.

### `Only 12% of the song was found in the video (minimum 50%)`

The video's soundtrack isn't the same recording as the chart's audio. Common causes:

- **Wrong recording:** a live chart paired with a studio video, or the other way round.
- **Different version:** a re-recording (for example "Taylor's Version"), a remix, a radio edit
  with a different mix, or a cover.
- **Wrong or modified video:** a lyric video with altered audio, a fan edit, or a reaction video.

Try to find a video with the same recording. If you're sure the match is right and only part of
the song is in the video, lower the threshold with `--min-coverage 0.3`. For a different
recording, sync by hand with `--offset-ms` (single-song mode), where video time = song time +
offset.

### Batch: `NO MATCH: no matching song in the library`

The video's title didn't look enough like any song's artist and title in your `song.ini`
files. You can:

- Pin it: put `URL | Song Folder Name` in your links file.
- Lower `--min-score` (default 80) to accept looser title matches. The audio check still
  protects against wrong matches.
- Check that `--library` points at the right folder.

### Batch: a video was matched to the wrong song

That can only happen if the wrong song's audio really is in the video, which is rare (for
example, the same song charted under a different title). Pin the right folder with
`URL | Song Folder Name` and re-run with `--force`.

### `'ffmpeg' was not found on PATH`

Install FFmpeg (see [Installation](installation.md)):

- **Windows:** `winget install Gyan.FFmpeg`, then **open a new terminal**. The installer
  updates `PATH`, but terminals that were already open don't see the change.
- **Debian/Ubuntu (including WSL):** `sudo apt install ffmpeg`.

### `chvideo` is not recognised / command not found

uv puts installed tools in a folder that may not be on your `PATH` yet. Run
`uv tool update-shell`, then open a new terminal. `uv tool dir --bin` shows the folder.

### `The video has no audio track to align with`

Some downloads are video-only. Try another upload of the same video, or set the offset by hand
with `--offset-ms`.

## Download errors

### `Sign in to confirm your age`

The video is age-restricted. See [Age-restricted videos](cookies.md) for how to export and pass a
`cookies.txt`.

### `Sign in to confirm you're not a bot` or `HTTP Error 429: Too Many Requests`

YouTube is rate-limiting your internet connection after many requests in a short time. It isn't
specific to the video, and it affects every tool on that connection. Wait a while (often about
an hour) and try again. A [cookies file](cookies.md) from a signed-in account makes it less
likely. For very large batches, run them in smaller pieces.

### `YouTube didn't accept the sign-in in cookies.txt`

The exported session has expired or been rotated. Export a fresh cookies file, following the
steps in [cookies.md](cookies.md) exactly: a private window that you close right after exporting.

### `HTTP Error 400: Bad Request` while using cookies

This is usually a cookie file with the wrong line endings. chvideo converts Windows line endings
automatically, so if you still see it, the file is probably damaged. Export it again.

## In Clone Hero

### The video doesn't show up

- Turn on **Settings → Gameplay → Song Videos**.
- Rescan your songs or restart the game. Clone Hero only notices new files when it scans.
- Make sure the song folder contains only one `video.*` file.
- Clone Hero on **Linux** only plays `.webm` (VP8) and `.ogv`. Don't use `--gpu` or
  `--format mp4` for a Linux install.

### The video is slightly early or late

The sync is measured from the audio and should be within a frame. If it still looks off, shift
it in-game: pause, open **Song Settings**, and adjust **Video Start Time**. Or re-run with
`--nudge-ms N`. Positive values make the video play ahead, negative values hold it back.

## GPU encoding

### `GPU not available (...); falling back to CPU`

chvideo couldn't start NVIDIA's encoder. The message in brackets says why. Check that:

- The machine has an NVIDIA GPU (NVENC is NVIDIA only).
- `nvidia-smi` works. On WSL2 it comes from the Windows NVIDIA driver; install or update the
  driver on Windows, not inside Linux.
- Your FFmpeg build includes `h264_nvenc`. Run `ffmpeg -hide_banner -encoders` and look for
  `nvenc`. The Windows `Gyan.FFmpeg` build and the Debian/Ubuntu packages include it.

### `GPU encode failed (...); re-encoding on the CPU`

The GPU encoder failed partway through the song, often because other software is using it
(game recording, streaming, ShadowPlay). That song is encoded on the CPU instead. The batch
carries on.
