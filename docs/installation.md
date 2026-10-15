# Installation

← [Back to README](../README.md)

chvideo is a Python package on [PyPI](https://pypi.org/project/chvideo/). You need two programs
to install and run it:

- [**uv**](https://docs.astral.sh/uv/) installs chvideo into its own isolated environment, so it
  can't conflict with anything else on your system. It also downloads a suitable Python (3.12 or
  newer) if you don't have one.
- [**FFmpeg**](https://ffmpeg.org/) does the audio decoding and video encoding.

- [Windows](#windows)
- [Linux](#linux)
- [Windows Subsystem for Linux (WSL2)](#windows-subsystem-for-linux-wsl2)
- [Upgrading](#upgrading)
- [Uninstalling](#uninstalling)
- [Using pipx instead of uv](#using-pipx-instead-of-uv)
- [Running from source](#running-from-source)

## Windows

Windows 10 and 11 are supported. Run these commands in **PowerShell**; Windows Terminal opens
PowerShell by default.

**1. Install FFmpeg**

```powershell
winget install Gyan.FFmpeg
```

This is the full FFmpeg build from gyan.dev. It includes everything chvideo uses, including the
NVIDIA encoder for `--gpu`. If you use Chocolatey or Scoop instead, `choco install ffmpeg` or
`scoop install ffmpeg` work too.

**2. Install uv**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or `winget install --id=astral-sh.uv -e`.

**3. Open a new terminal**, so the updated `PATH` is picked up. Then install chvideo:

```powershell
uv tool install chvideo
```

**4. Check it works:**

```powershell
chvideo --help
ffmpeg -version
```

If `chvideo` isn't recognised, run `uv tool update-shell` and open a new terminal again.

### Windows tips

- **Quoting.** Put paths in single quotes. If a folder name itself contains a `'`, double it:
  `'C:\Songs\Wheeler Walker Jr. - Money ''N'' Bitches'`.
- **Song folder.** Pass the folder where your Clone Hero songs live, as added in Clone Hero's
  settings, for example:

  ```powershell
  chvideo batch links.txt --library 'C:\Games\Clone Hero\Songs'
  ```

- **Defaults.** To avoid typing `--library` (or `--cookies`) every time, set it once:

  ```powershell
  setx CHVIDEO_LIBRARY "C:\Games\Clone Hero\Songs"
  ```

  `setx` only affects terminals opened afterwards.
- **GPU encoding** (`--gpu`) works natively with an NVIDIA card and an up-to-date NVIDIA driver.
- **Cache.** Downloads and work files go to `%LOCALAPPDATA%\chvideo\Cache`.

## Linux

**1. Install FFmpeg** with your distribution's package manager:

| Distribution | Command |
| --- | --- |
| Debian / Ubuntu / Mint | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` (from [RPM Fusion](https://rpmfusion.org/Configuration); Fedora's own `ffmpeg-free` lacks some encoders) |
| Arch / Manjaro | `sudo pacman -S ffmpeg` |
| openSUSE | `sudo zypper install ffmpeg` (from [Packman](https://en.opensuse.org/Additional_package_repositories#Packman)) |

chvideo needs FFmpeg's `libvpx` (VP8) encoder. Check that it's there:
`ffmpeg -hide_banner -encoders | grep libvpx`.

**2. Install uv:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Some distributions also package it (`pacman -S uv`, `dnf install uv`).

**3. Install chvideo:**

```bash
uv tool install chvideo
chvideo --help
```

If `chvideo` isn't found, run `uv tool update-shell` and open a new terminal.

### Linux tips

- **Quoting.** Put paths in single quotes. In bash, a `!` inside double quotes triggers history
  expansion, and Clone Hero folders like `!Individual Songs` are common.
- **Defaults.** Put them in `~/.bashrc` (or your shell's equivalent):

  ```bash
  export CHVIDEO_LIBRARY='/path/to/Clone Hero/Songs'
  export CHVIDEO_COOKIES=~/youtube-cookies.txt
  ```

- **GPU encoding** (`--gpu`) needs the proprietary NVIDIA driver and an FFmpeg built with NVENC.
  Debian's and Ubuntu's packages are. Check with `ffmpeg -hide_banner -encoders | grep nvenc`.
- **Cache.** Downloads and work files go to `~/.cache/chvideo`, or `$XDG_CACHE_HOME/chvideo`
  if that's set.

## Windows Subsystem for Linux (WSL2)

Follow the [Linux](#linux) steps inside your WSL distribution. Two things are specific to WSL:

- **Windows drives** appear under `/mnt/`, so a Clone Hero library on `C:` is somewhere like
  `/mnt/c/Users/<you>/Documents/Clone Hero/Songs`. Scanning a library through `/mnt/` is slower
  than a native Linux folder, but fine in practice: a few seconds for about 700 songs.
- **GPU encoding works.** WSL2 passes the NVIDIA GPU through using the normal Windows NVIDIA
  driver. Don't install an NVIDIA driver inside Linux. `nvidia-smi` should work in WSL out of
  the box.

If Clone Hero runs on Windows, you can also just install chvideo natively on Windows (above)
and skip WSL entirely.

## Upgrading

```bash
uv tool upgrade chvideo
```

YouTube changes regularly, and old versions of yt-dlp (chvideo's downloader) eventually stop
working. Upgrading chvideo also upgrades yt-dlp, so **when downloads start failing, upgrade
first.**

## Uninstalling

```bash
uv tool uninstall chvideo
```

Then delete the cache folder: `%LOCALAPPDATA%\chvideo` on Windows, `~/.cache/chvideo` on Linux.
Videos chvideo added to your songs stay where they are.

## Using pipx instead of uv

If you already use [pipx](https://pipx.pypa.io/) and have Python 3.12 or newer:

```bash
pipx install chvideo
pipx upgrade chvideo
```

## Running from source

```bash
git clone https://github.com/<your-username>/chvideo.git
cd chvideo
uv run chvideo --help    # runs the checked-out code
uv run pytest            # the test suite (the end-to-end tests need FFmpeg)
uv tool install --editable .   # optional: a `chvideo` command that tracks your checkout
```
