# Usage

← [Back to README](../README.md)

- [Running chvideo](#running-chvideo)
- [One song](#one-song)
- [Many songs: `chvideo batch`](#many-songs-chvideo-batch)
- [Finding songs without a video: `chvideo missing`](#finding-songs-without-a-video-chvideo-missing)
- [Finding videos automatically: `chvideo auto`](#finding-videos-automatically-chvideo-auto)
- [Seeing the video in Clone Hero](#seeing-the-video-in-clone-hero)
- [Fine-tuning the sync](#fine-tuning-the-sync)
- [Encoding: quality, size and speed](#encoding-quality-size-and-speed)
- [Option reference](#option-reference)
- [Environment variables](#environment-variables)
- [Cache and disk space](#cache-and-disk-space)
- [Paths and shell quoting](#paths-and-shell-quoting)

## Running chvideo

After installing (see [Installation](installation.md)) the command is `chvideo`, in PowerShell on
Windows and in any shell on Linux. Examples use Linux paths; on Windows use paths like
`'C:\Games\Clone Hero\Songs\Artist - Song (Charter)'`. Everything else is identical. When
running from a source checkout, use `uv run chvideo` instead.

## One song

```bash
chvideo '/path/to/Songs/Artist - Song (Charter)' 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The first argument is a Clone Hero song folder: the one containing `song.ini`,
`notes.chart`/`notes.mid` and the audio stems. The second is a video URL (anything
[yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)) or a local
video file.

chvideo then:

1. **Downloads the video.** It uses the best quality up to `--max-height`, default 1080p.
2. **Aligns it.** It mixes the chart's stems (`song`, `guitar`, `bass`, `rhythm`, `keys`,
   `vocals*`, `drums*`; never `crowd`) and finds them in the video's soundtrack.
3. **Prints a report:**

   ```text
   Song length 4:03.008, video soundtrack 3:54.243, 99% of the song found in the video
   song 0:04.377 - 3:57.633: video offset -3724.8 ms  match 0.70  precision ±3.7 ms (22 checks)
   (equivalent to video_start_time = -3725 on the original video)
   ```

   An edited music video shows several lines here, one per matched section.
4. **Renders and installs** `video.webm` into the song folder, and sets `video_start_time = 0` in
   `song.ini`.

Useful variations:

```bash
# See the alignment without rendering anything
chvideo SONG_DIR URL --analyze-only

# Render, but leave the song folder untouched; also write a preview with the chart's audio
# muxed in, so you can check the sync in any media player first
chvideo SONG_DIR URL --dry-run --preview

# Replace a video the song already has (the old one is kept as video.<ext>.bak)
chvideo SONG_DIR URL --force

# The video has different audio (e.g. a live chart with a studio video): set the offset by hand.
# video time = song time + offset
chvideo SONG_DIR URL --offset-ms -2500
```

## Many songs: `chvideo batch`

```bash
chvideo batch links.txt --library '/path/to/Songs'
```

Inputs can be any mix of:

- **Video URLs.**
- **Playlist URLs.** Every video in the playlist is used.
- **YouTube searches**, such as `ytsearch1:Artist Song official video`, which use the first
  result.
- **Local video files.**
- **Text files listing any of the above**, one per line.

In a text file, blank lines and lines starting with `#` are ignored:

```text
# plain links: matched to a song automatically
https://www.youtube.com/watch?v=VIDEO_ID

# a whole playlist
https://www.youtube.com/playlist?list=PLAYLIST_ID

# a search: the first result is used
ytsearch1:Alien Ant Farm Smooth Criminal official video

# pin a video to a specific song (a path, or a folder name inside --library)
https://www.youtube.com/watch?v=VIDEO_ID | Artist - Song (Charter)
```

A tab also works as the separator in place of ` | `. A starter file is in
[`links.txt.example`](../links.txt.example).

### What batch mode does

1. **Scans the library.** `--library` is searched recursively for song folders, and each
   `song.ini` supplies the artist and title.
2. **Reads each video's title and channel.** Nothing is downloaded yet.
3. **Fuzzy-matches each video to a song and shows a plan:**

   ```text
   Plan:
       1. 3 Doors Down - Kryptonite (Official Video)  ->  3 Doors Down - Kryptonite  [match 100]
       2. Paramore: Still Into You [OFFICIAL VIDEO]   ->  SKIP: Paramore - Still Into You already has a video (use --force)
       3. Linkin Park - Numb (Official Music Video)   ->  NO MATCH: no matching song in the library
   ```

   It then asks before starting. `--plan` shows the plan and stops; `-y` skips the question.
4. **Confirms every match by audio.** A video is only installed if the song's audio is really in
   it. If the best title match fails, the next-best candidate is tried. A wrong title match
   can't install the wrong video.
5. **Processes the videos one by one.** The next ones (`--parallel-downloads`, default 2)
   download in the background while the current one encodes.
6. **Prints a summary** of what was synced, skipped and failed. Failed links are listed so you
   can retry them, for example with a `| folder` pin if the match was wrong.

More details:

- **Duplicate charts:** if a song is charted more than once in your library, every chart of it
  gets the video. Each chart is audio-checked separately.
- **Existing videos:** songs that already have a video are skipped without downloading, unless
  you pass `--force`.
- **Match threshold:** titles are matched on artist and song name. Chart annotations like
  "(Live)" or "(X cover)" count slightly against a match. `--min-score` (default 80) sets how
  close a title must be to be considered at all.

## Finding songs without a video: `chvideo missing`

```bash
chvideo missing '/path/to/Songs'
```

This scans the folder recursively, the same way `chvideo batch --library` does, and lists every
song that has no `video.*` file:

```text
Alien Ant Farm - Smooth Criminal
Papa Roach - Scars  (2 charts)
...

433 of 754 song folders have no video (426 distinct songs).
```

Charts of the same song are listed once, with a count. The folder argument defaults to
`$CHVIDEO_LIBRARY`.

The list goes to stdout and the count to stderr, so you can save just the list:

```bash
chvideo missing '/path/to/Songs' > missing.txt
```

`--paths` prints each song folder's full path instead, one per line (every chart separately).
These work as `| folder` pins in a batch links file.

## Finding videos automatically: `chvideo auto`

```bash
chvideo auto '/path/to/Songs'
```

For every song that `chvideo missing` would list, this searches YouTube for the official music
video and syncs it, with no links file needed. Not every song has a music video. For songs that
don't, nothing is installed, and you can [block them](#blocking-songs-that-have-no-video) so later
runs skip them.

1. **Searches YouTube** for `Artist Song official music video`. Chart annotations such as
   `(WaveGroup)` or `(Britney Spears cover)` are left out of the search.
2. **Drops what clearly isn't the music video**, judged from the title, channel and description:
   - lyric videos, visualizers and official-audio uploads
   - auto-generated uploads ("Artist - Topic", "Provided to YouTube by …")
   - live, acoustic and remix versions, and fan-filmed concerts
   - covers, drum/guitar/bass playthroughs, reactions, lessons, interviews, commentary, and
     fan-made or student-made videos
   - uploads where the uploader names themselves in the title, such as
     `Alex Rudinger - Bad Wolves - "Officer Down"` on Alex Rudinger's channel (a drum playthrough)
   - videos too short to contain the song
3. **Checks the rest are legitimate.** A video is used only if it comes from:
   - **the artist's own channel**, or **a label or VEVO channel**. These are trusted as they are.
   - **any other channel** (a director's, a production company's, a re-upload), if its full
     description has the credits official videos carry ("Directed by …", "Director:",
     "Music video by X performing Y", "Production company", …) or its title says "Official".
     Fan disclaimers ("all rights go to the original owners", "I don't own this"), "my cover"
     and drum/guitar gear endorsements rule a video out.

   Checking the full description costs one extra request per video, so it's only done for
   uploads that aren't from the artist or a label.
4. **Shows a plan and asks before starting**, like `chvideo batch`. Songs where nothing passed say
   why:

   ```text
   Plan:
       1. Wolfmother - Woman (Official Video)  ->  Wolfmother - Woman (Harmonix)  (+2 fallback videos)
       2. Bad Wolves - Officer Down            ->  FAILED: no music video found (rejected: doesn't look official (fan re-upload?) x2)
   ```

5. **Confirms every video by audio.** A video is only installed if the song's audio is really in
   it. If the top pick fails, the next one is tried, up to `--tries` (default 3) per song.

Charts of a specific version work too. A chart named `Song (Live)` or `Song (Acoustic)` only
accepts videos whose title says so, and the search leaves out "official music video" so that it
finds those versions.

For older songs whose label never uploaded the video, fan re-uploads are often the only copy.
`--allow-unofficial` accepts uploads that don't look official. Lyric videos, covers,
playthroughs and the rest of step 2 are still left out.

Start small to see how the picks look before running it on everything:

```bash
# Just show what would be picked for the first 20 songs
chvideo auto '/path/to/Songs' --plan --limit 20

# One artist or song
chvideo auto '/path/to/Songs' --only 'wolfmother'
```

Songs where no video passed are listed at the end as `<last video or search> | <song folder>`
lines. Put them in a file, swap in the right video URLs, and run it with `chvideo batch`.

### Blocking songs that have no video

Some songs just don't have a music video. So that `chvideo auto` doesn't search for them on every
run, it offers to block them at the end:

```text
No matching video was found for 2 song(s):
  Van Morrison - Brown Eyed Girl
  Wheeler Walker Jr. - Sniffers Row
Block them so `chvideo auto` skips them next time? [y/N]
```

Only songs that failed for good are offered: there was no usable search result, or every video
tried failed the audio check. Download errors, rate limiting and encoding failures are never
blocked, because a later run may succeed. With `-y` nothing is blocked unless you also pass
`--block-failed`, which blocks without asking.

Blocked songs are listed in `chvideo-blocked.txt` in your songs folder. Each line is a song folder
relative to that folder, under a comment saying when and why it was blocked:

```text
# 2026-10-22: no music video found in the search results
Wheeler Walker Jr. - Sniffers Row
```

- **To unblock a song**, delete its line.
- **To block one by hand**, add its folder.
- **To try all blocked songs once**, pass `--retry-blocked`.

`chvideo missing` still lists blocked songs, since they still have no video.

> [!NOTE]
> Every song costs a YouTube search, sometimes a few metadata checks, and a download per try. On
> a big library YouTube may start rate-limiting you ("Sign in to confirm you're not a bot"). When
> that happens, chvideo stops: it keeps the songs it has already found videos for, and leaves the
> rest for a later run without marking them failed. Wait a while (often an hour), use `--limit`
> to work through the library in chunks, or pass `--cookies` (see [cookies.md](cookies.md)).

## Seeing the video in Clone Hero

In Clone Hero, open **Settings → Gameplay** and turn on **Song Videos**. Clone Hero only picks up
new files when it scans your songs, so rescan or restart the game after adding videos.

A song folder should contain only one `video.*` file. chvideo renames any existing one to
`.bak` when you use `--force`.

## Fine-tuning the sync

The sync is measured from the audio, so it should be right as installed. If you'd still like to
shift the video, there are two ways:

- **In-game:** start the song, pause, open **Song Settings**, and adjust **Video Start Time**.
  Up/Down changes it by 1 ms, Left/Right by 100 ms.
- **With chvideo:** `--nudge-ms N` writes `video_start_time = N` into `song.ini`.

In both, positive values make the video play *ahead* (it starts further in), and negative values
hold it back.

## Encoding: quality, size and speed

| | CPU (default) | GPU (`--gpu`) |
| --- | --- | --- |
| Output | VP8 `video.webm` | H.264 `video.mp4` |
| Works on | All platforms Clone Hero runs on | Windows and macOS |
| Time to process a 4-min 1080p song* | ~23 s | ~16 s |
| Quality at the same file size | Good | Noticeably better |

\* Excluding the download and the audio alignment (about 5–10 s). Measured on a Ryzen 9 5900X
and an RTX 3080. Both outputs are about 115–120 MB for a 4-minute
1080p song.

- **`--gpu`** uses NVIDIA NVENC. If no usable NVIDIA GPU is found, or a GPU encode fails partway
  through (for example because recording or streaming software has the encoder busy), chvideo
  says so and encodes on the CPU instead. NVENC works under WSL2 with the normal Windows
  NVIDIA driver; nothing extra is needed inside Linux. GPUs can't encode VP8, which is why
  `--gpu` produces `.mp4`. The Clone Hero wiki recommends VP8 `.webm` as the safest format, so
  the CPU encode stays the default.
- **`--max-height 720`** makes smaller files and faster encodes.
- **`--quality N`** sets the encoder CRF. Lower means better quality and bigger files; the
  default is 10.
- **`--jobs N`** sets the number of parallel encoder processes. The default is one per physical
  CPU core, or 4 GPU sessions with `--gpu`.
- **`--format mp4`** encodes H.264 on the CPU (libx264).

## Option reference

### Shared by `chvideo`, `chvideo batch` and `chvideo auto`

| Option | Default | Description |
| --- | --- | --- |
| `--dry-run` | | Analyse and render, but don't touch the song folder |
| `--force` | | Replace an existing video; the old one is kept as `video.<ext>.bak` |
| `--nudge-ms N` | `0` | Value written to `video_start_time` (see [Fine-tuning](#fine-tuning-the-sync)) |
| `--gpu` | | Encode on an NVIDIA GPU (H.264 `.mp4`), falling back to the CPU |
| `--format {webm,mp4}` | `webm` | Output format for CPU encoding |
| `--max-height N` | `1080` | Maximum output height; also the download quality limit |
| `--fps N` | source (max 60) | Output frame rate |
| `--quality N` | `10` | Encoder CRF, lower = better/larger |
| `--jobs N` | cores | Parallel encoder processes |
| `--gap-fill {continue,black}` | `continue` | What to show where the song has audio the video lacks: keep the surrounding footage playing, or black |
| `--min-coverage F` | `0.5` | Fraction of the song that must be found in the video |
| `--cookies FILE` | `$CHVIDEO_COOKIES` | `cookies.txt` for age-restricted videos (see [cookies.md](cookies.md)) |
| `--keep-downloads` | | Keep downloaded videos in the cache after installing |
| `--refresh` | | Re-download even if the video is cached |

### `chvideo` (one song) only

| Option | Description |
| --- | --- |
| `--analyze-only` | Print the alignment and stop |
| `--preview [PATH]` | Also write a copy with the chart's audio muxed in, for checking in a media player |
| `--offset-ms N` | Skip audio analysis and use a fixed offset: video time = song time + N ms |

### `chvideo batch` only

| Option | Default | Description |
| --- | --- | --- |
| `--library DIR` | `$CHVIDEO_LIBRARY` | Songs folder to match videos against (searched recursively) |
| `--plan` | | Show the video → song plan and stop |
| `-y`, `--yes` | | Don't ask for confirmation |
| `--min-score N` | `80` | Minimum title-match score (0–100) |
| `--preview` | | Write a preview per song into the cache |
| `--parallel-downloads N` | `2` | Videos downloaded ahead while encoding |

### `chvideo auto` only

| Option | Default | Description |
| --- | --- | --- |
| `LIBRARY` | `$CHVIDEO_LIBRARY` | Songs folder (searched recursively) |
| `--only TEXT` | | Only songs whose `Artist - Song` contains TEXT |
| `--limit N` | | Only the first N songs without a video (alphabetically) |
| `--tries N` | `3` | Search results to try per song before giving up |
| `--min-score N` | `80` | Minimum title-match score (0–100) for a search result |
| `--allow-unofficial` | | Also accept uploads that don't look official (fan re-uploads); covers, lyric videos etc. are still left out |
| `--block-failed` | | Block songs no video could be found for without asking (see [Blocking](#blocking-songs-that-have-no-video)) |
| `--retry-blocked` | | Also try the songs listed in `chvideo-blocked.txt` |
| `--plan`, `-y`, `--preview`, `--parallel-downloads N` | | As for `chvideo batch` |

### `chvideo missing`

| Option | Default | Description |
| --- | --- | --- |
| `LIBRARY` | `$CHVIDEO_LIBRARY` | Songs folder to scan (searched recursively) |
| `--paths` | | Print full song-folder paths instead of `Artist - Song` |

## Environment variables

| Variable | Used for |
| --- | --- |
| `CHVIDEO_LIBRARY` | Default for `--library` and the `chvideo missing` / `chvideo auto` folder |
| `CHVIDEO_COOKIES` | Default for `--cookies` |
| `CHVIDEO_CACHE` | Cache folder (overrides the default below) |
| `XDG_CACHE_HOME` | Linux only: base of the default cache folder |

Setting them permanently:

```powershell
# Windows (affects terminals opened afterwards)
setx CHVIDEO_LIBRARY "C:\Games\Clone Hero\Songs"
setx CHVIDEO_COOKIES "$env:USERPROFILE\youtube-cookies.txt"
```

```bash
# Linux: add to ~/.bashrc
export CHVIDEO_LIBRARY='/path/to/Clone Hero/Songs'
export CHVIDEO_COOKIES=~/youtube-cookies.txt
```

## Cache and disk space

The cache folder is:

| OS | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\chvideo\Cache` |
| Linux | `~/.cache/chvideo` (or `$XDG_CACHE_HOME/chvideo`) |
| Any | `$CHVIDEO_CACHE`, if set |

What goes in it:

- **Downloads** go to its `downloads` folder. A download is deleted after it's installed
  successfully, unless you pass `--keep-downloads`. Downloads that failed, or were only used for
  `--dry-run`, are kept, so a retry doesn't download them again.
- **Dry runs and previews** are written to its `work` folder.

It's always safe to delete the whole cache folder.

## Paths and shell quoting

**All shells:** put paths and URLs in single quotes. Unquoted URLs containing `&` get cut off.

**PowerShell (Windows):**

- Single-quoted text is taken literally, so `!`, `&`, `$` and spaces are all fine.
- A folder name containing `'` needs it doubled:
  `'C:\Songs\Wheeler Walker Jr. - Money ''N'' Bitches'`.
- Tab completion also quotes paths for you.

**bash (Linux / WSL):**

- Inside *double* quotes, bash expands `!` (history expansion). That breaks folder names like
  `!Individual Songs`, so stick to single quotes.
- A folder name containing `'`: press Tab to let bash complete and escape the path, or write the
  quote as `'\''`. For example: `'/Songs/Wheeler Walker Jr. - Money '\''N'\'' Bitches'`.
- **WSL:** Windows drives appear under `/mnt/<letter>/`. For example, `C:\Users\you\Documents`
  is `/mnt/c/Users/you/Documents`.
