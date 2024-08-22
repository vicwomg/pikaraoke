# PiKaraoke

PiKaraoke is a "KTV"-style karaoke song search and queueing system. It connects to your TV, and shows a QR code for computers and smartphones to connect to a web interface. From there, multiple users can seamlessly search your local track library, queue up songs, add an endless selection of new karaoke tracks from YouTube, and more. Works on Raspberry Pi, OSX, Windows, and Linux!

If you want to support this project with a little monetary tip, it's much appreciated: <br/>
<a href="https://www.buymeacoffee.com/vicwomg" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>

## Table of Contents

- [Features](#features)
- [Supported Devices / OS](#supported-devices--os)
- [Get Started](#get-started)
  - [Install](#install)
  - [Run](#run)
  - [More Options](#more-options)
- [Screenshots](#screenshots)
  - [TV](#tv)
  - [Web interface](#web-interface)
- [Troubleshooting](#troubleshooting)
- [Install from Repo](#install-from-repository-legacy)

## Features

| **Feature**                 | **Description**                                               |
| --------------------------- | ------------------------------------------------------------- |
| Web Interface               | Multiple users can queue tracks from their smartphones        |
| Player/Splash Screen        | Connection QR code and "Next up" display                      |
| Searching/Browsing          | Browse a local song library                                   |
| Adding New Songs            | Add new songs from Youtube                                    |
| mp3 + cdg Support           | Includes compressed .zip bundles                              |
| Playback Controls           | Pause, Skip, Restart, and volume control                      |
| File Management             | Advanced editing of downloaded file names                     |
| Queue Management            | Manage the song queue and change the order                    |
| Key Change / Pitch Shifting | Adjust the pitch of songs                                     |
| Admin Mode                  | Lock down features with admin mode                            |
| Headless Mode               | Run a dedicated server and stream pikaraoke to remote browser |

## Supported Devices / OS

- Raspberry Pi
  - Requires a Raspberry Pi Model 3 or higher
  - Desktop OS required for standalone/headed mode
  - For Pi 3: 32-bit Bullseye OS and overclocking is recommended for smoother playback.
- OSX
- Windows
- Linux

## Get Started

### Install required programs

Raspberry Pi OS / Debian-based distros:

```
sudo apt-get install ffmpeg -y
sudo apt-get install chromium-browser -y
sudo apt-get install chromium-chromedriver -y
```

Windows / OSX / Linux:

- FFmpeg 6.0 or greater: https://ffmpeg.org/download.html
- Chrome Browser: http://google.com/chrome (only required for headed mode)

### Install pikaraoke

Optional: create a virtual environment. Recommended if you might have conflicting python programs installed. Probably not a concern for many users. (See: https://docs.python.org/3/library/venv.html)

Install pikaraoke from PyPi on the host:

```sh
# Install pikaraoke from PyPi
pip install pikaraoke
```

### Run

Pikaraoke is now installed in `$PATH` with the command line interface `pikaraoke`. Start by
calling the pikaraoke command.

```sh
# Run pikaraoke
pikaraoke
```

### More Options

See the help command `pikaraoke --help` for available options.

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

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for help with issues.

## Install from Repository (Legacy)

See [README](../scripts/README.md) for how to install pikaraoke cloning this repo and using the
scripts. This is a legacy method and may no longer work.
