# PiKaraoke

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-green.svg)](https://conventionalcommits.org)

PiKaraoke is a "KTV"-style karaoke song search and queueing system. It connects to your TV, and shows a QR code for computers and smartphones to connect to a web interface. From there, multiple users can seamlessly search your local track library, queue up songs, add an endless selection of new karaoke tracks from YouTube, and more. Works on Raspberry Pi, OSX, Windows, and Linux!

Features: dedicated player/splash screen, mobile-friendly web interface, song searching/browsing, adding new songs from YouTube, mp3 + cdg support, playback controls, queue management, key change / pitch shifting, filename management, admin mode, headless mode

Pikaraoke is independently developed and maintained. If you want to support this project with a little monetary tip, it's much appreciated: <br/><br/>
<a href="https://www.buymeacoffee.com/vicwomg" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>

## Table of Contents

- [Supported Devices / OS / Platforms](#supported-devices--os--platforms)
- [Quick Install](#quick-install)
- [Manual Installation](#manual-installation)
- [Docker](#docker-instructions)
- [Screenshots](#screenshots)
- [Developing pikaraoke](#developing-pikaraoke)
- [Troubleshooting](#troubleshooting)

## Supported Devices / OS / Platforms

- Raspberry Pi
  - Requires a Raspberry Pi Model 3 or higher
  - Bookworm Desktop OS required for standalone/headed mode
  - For Pi 3: overclocking is recommended for smoother playback
- OSX
- Windows
- Linux

## Quick Install

For a streamlined installation that handles all dependencies (python, pipx, ffmpeg, deno, yt-dlp) and installs PiKaraoke, run the following in your terminal:

### Linux & macOS

```sh
curl -fsSL https://raw.githubusercontent.com/vicwomg/pikaraoke/master/build_scripts/install/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/vicwomg/pikaraoke/master/build_scripts/install/install.ps1 | iex
```

After installation, you can launch pikaraoke from the command line with `pikaraoke` or from a desktop shortcut (if specified).

You can rerun the above command to update a previous installation also.

## Manual installation (advanced)

### Prerequisites

- A modern web browser (Chrome/Chromium/Edge recommended)
- Python 3.10 or greater: [Python downloads](https://www.python.org/downloads/)
- FFmpeg: [FFmpeg downloads](https://ffmpeg.org/download.html)
- A js runtime installed to your PATH. [Node.js](https://nodejs.org/en/download/) is most common, [Deno](https://deno.com/) is probably easiest for non-developers.

### Install pikaraoke via pipx

We recommend installing pikaraoke via [pipx](https://github.com/pypa/pipx). You may alternately use the standard python `pip` installer if you are familiar with virtual environments or you are not concerned with global package isolation.

```sh
pipx install pikaraoke
```

Run pikaraoke with:

```sh
pikaraoke
```

This will start pikaraoke in headed mode, and open the default browser with the splash screen. You can then connect to the QR code via your mobile device and start downloading and queueing songs.

### Upgrading

To upgrade to the latest version of pikaraoke, run:

```sh
pipx upgrade pikaraoke
```

### More Options

See the help command `pikaraoke --help` for available options.

## Docker instructions

For Docker users, you can get going with one command. Note that for best results you should map the port, supply your actual LAN IP, and set some persistence volumes for songs and settings (for simplicity, this example sets them to ~):

```sh
docker run -p 5555:5555 \
  -v ~/pikaraoke-songs:/app/pikaraoke-songs \
  -v ~/.pikaraoke:/home/pikaraoke/.pikaraoke \
  vicwomg/pikaraoke:latest \
  -u http://<YOUR_LAN_IP>:5555
```

For more information and a configurable docker-compose example, [see official Dockerhub repo](https://hub.docker.com/r/vicwomg/pikaraoke)

## Screenshots

<div style="display: flex">
<img width="250" alt="pikaraoke-nowplaying" src="https://user-images.githubusercontent.com/4107190/95813193-2cd5c180-0ccc-11eb-89f4-11a69676dc6f.png">
<img width="250" alt="pikaraoke-queue" src="https://user-images.githubusercontent.com/4107190/95813195-2d6e5800-0ccc-11eb-8f00-1369350a8a1c.png">
<img width="250"  alt="pikaraoke-browse" src="https://user-images.githubusercontent.com/4107190/95813182-27787700-0ccc-11eb-82c8-fde7f0a631c1.png">
<img width="250"  alt="pikaraoke-search1" src="https://user-images.githubusercontent.com/4107190/95813197-2e06ee80-0ccc-11eb-9bf9-ddb24d988332.png">
<img width="250"  alt="pikaraoke-search2" src="https://user-images.githubusercontent.com/4107190/95813190-2ba49480-0ccc-11eb-84e3-f902cbd489a2.png">
</div>
<img width="400" alt="pikaraoke-tv2" src="https://user-images.githubusercontent.com/4107190/95813564-019fa200-0ccd-11eb-95e1-57a002c357a3.png">
  </p>

## Developing pikaraoke

The Pikaraoke project utilizes `uv` for dependency management and local development.

- Install [uv](https://github.com/astral-sh/uv)
- Git clone this repo

From the pikaraoke directory :

```sh
# install dependencies and run pikaraoke
uv run pikaraoke
```

See the [Pikaraoke development guide](https://github.com/vicwomg/pikaraoke/wiki/Pikaraoke-development-guide) for more details.

## Troubleshooting and guides

See the [TROUBLESHOOTING wiki](https://github.com/vicwomg/pikaraoke/wiki/FAQ-&-Troubleshooting) for help with issues.

There are also some great guides [on the wiki](https://github.com/vicwomg/pikaraoke/wiki/) to running pikaraoke in all manner of bizarre places including Android, Chromecast, and embedded TVs!
