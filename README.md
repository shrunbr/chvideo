# chvideo

**Sync music videos to your Clone Hero songs automatically.**

chvideo downloads a song's music video, finds exactly where the chart's audio sits in the video's
soundtrack, and renders a video that plays in sync with the chart while you play. Point it at one
song, or hand it a list of links and let it work out which video belongs to which song in your
library.

[![PyPI](https://img.shields.io/pypi/v/chvideo)](https://pypi.org/project/chvideo/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![Windows | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)

## Features

- **Synced by audio, not by ear.** The video's soundtrack is aligned to the chart's own audio
  to about a millisecond. The sync is baked into the video, so there's no `video_start_time`
  tweaking.
- **Handles edited music videos.** Skits, extended intros and cut sections are detected. Each
  part of the song is matched on its own and spliced back into sync.
- **Batch mode.** Give it links, playlists or YouTube searches. It matches each video to a song
  in your library by title, and confirms the match by audio before installing anything.
- **Rejects wrong matches.** Live versions, re-recordings and wrong videos are refused instead
  of being synced badly.
- **Fast.** Encoding runs in parallel across your CPU cores, or on an NVIDIA GPU with `--gpu`.
- **Safe with your files.** An existing video is only replaced when you ask, and a backup is
  kept. `song.ini` is backed up before it's edited.
- **Age-restricted videos** work with a `cookies.txt` file.

## Requirements

- **Windows 10/11 or Linux** (including WSL2).
- [**uv**](https://docs.astral.sh/uv/). It installs chvideo, and Python 3.12+ if you don't
  already have it.
- [**FFmpeg**](https://ffmpeg.org/).
- *Optional:* an NVIDIA GPU for `--gpu`.

## Installation

Install FFmpeg and uv, then chvideo:

**Windows** (PowerShell):

```powershell
winget install Gyan.FFmpeg
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a new terminal, then:
uv tool install chvideo
```

**Linux** (Debian/Ubuntu shown; use your distribution's package manager):

```bash
sudo apt install ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install chvideo
```

The [installation guide](docs/installation.md) covers the details: other Linux distributions,
pipx, upgrading, uninstalling, and running from source.

## Quick start

**One song:**

```powershell
# Windows
chvideo 'C:\Games\Clone Hero\Songs\Artist - Song (Charter)' 'https://www.youtube.com/watch?v=VIDEO_ID'
```

```bash
# Linux
chvideo '/path/to/Songs/Artist - Song (Charter)' 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The song folder gets a synced `video.webm`. In Clone Hero, turn on
**Settings → Gameplay → Song Videos**, then rescan your songs or restart the game.

**Many songs:**

```bash
chvideo batch links.txt --library '/path/to/Songs'
```

`links.txt` holds one video link, playlist or search per line; see
[`links.txt.example`](links.txt.example). chvideo shows which video goes to which song and asks
before it starts.

**Which songs still need a video:**

```bash
chvideo missing '/path/to/Songs'
```

> [!TIP]
> Put paths and URLs in single quotes, in both PowerShell and bash. In bash, a `!` inside
> double quotes (common in Clone Hero folder names) triggers history expansion. More quoting
> tips are in [Usage](docs/usage.md#paths-and-shell-quoting).

## Documentation

| Guide | What's in it |
| --- | --- |
| [Installation](docs/installation.md) | Windows and Linux setup, upgrading, uninstalling, running from source |
| [Usage](docs/usage.md) | Every option, the batch file format, GPU encoding, fine-tuning sync |
| [Age-restricted videos](docs/cookies.md) | Exporting and using a YouTube `cookies.txt` |
| [How it works](docs/how-it-works.md) | The alignment algorithm, rendering, accuracy and benchmarks |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and how to fix them |

## How it works

1. **Download** the video with [yt-dlp](https://github.com/yt-dlp/yt-dlp).
2. **Align** the video's soundtrack against a mix of the chart's audio stems, section by section,
   to about 1 ms.
3. **Render** a new video in which frame *t* is exactly song time *t*: trimmed, delayed and
   spliced as needed. The output is VP8 `.webm`, the format Clone Hero supports on every platform.
4. **Install** it as `video.webm` in the song folder and set `video_start_time = 0`.

The details are in [How it works](docs/how-it-works.md).

## Limitations

- The video's soundtrack must be the **same recording** as the chart audio. A live chart can't be
  synced to a studio music video automatically; use `--offset-ms` to sync those by hand.
- `.sng` song packages aren't supported. Use extracted song folders.
- GPU encoding supports NVIDIA (NVENC) only.

## Development

```bash
git clone https://github.com/<your-username>/chvideo.git
cd chvideo
uv run pytest          # needs FFmpeg for the end-to-end tests
uv run chvideo --help
```

CI runs the tests on Windows and Linux with Python 3.12 and 3.13.

## License

chvideo is released under the [MIT License](LICENSE).

It depends on third-party software under its own licenses. FFmpeg is installed separately and
run as an external program. The Python dependencies are permissively licensed (MIT, BSD,
Apache-2.0, Unlicense, MPL-2.0). The one exception is `mutagen` (GPL-2.0-or-later), which
yt-dlp installs as an optional extra for embedding thumbnails; chvideo never uses it and doesn't
redistribute it.

## Disclaimer

chvideo is an independent project and isn't affiliated with Clone Hero or YouTube. Only download
videos you have the right to download, and respect YouTube's Terms of Service and the rights of
the video owners.

## AI Disclaimer

This project was created using Claude Code.