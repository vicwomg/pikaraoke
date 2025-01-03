# PiKaraoke

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-green.svg)](https://conventionalcommits.org)

PiKaraoke is a "KTV"-style karaoke song search and queueing system. It connects to your TV, and shows a QR code for computers and smartphones to connect to a web interface. From there, multiple users can seamlessly search your local track library, queue up songs, add an endless selection of new karaoke tracks from YouTube, and more. Works on Raspberry Pi, OSX, Windows, and Linux!

If you want to support this project with a little monetary tip, it's much appreciated: <br/>
<a href="https://www.buymeacoffee.com/vicwomg" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>

## Table of Contents

- [Features](#features)
- [Supported Devices / OS](#supported-devices--os)
- [Get Started](#get-started)
- [Screenshots](#screenshots)
- [Developing pikaraoke](#developing-pikaraoke)
- [Troubleshooting](#troubleshooting)

## Features

| **Feature**                 | **Description**                                               |
| --------------------------- | ------------------------------------------------------------- |
| Web Interface               | Multiple users can queue tracks from their smartphones        |
| Player/Splash Screen        | Connection QR code and song queue metadata                    |
| Searching/Browsing          | Search and rowse a local song library                         |
| Adding New Songs            | Add new songs from Youtube                                    |
| mp3 + cdg Support           | CDG file support, supports compressed .zip bundles            |
| Playback Controls           | Pause, Skip, Restart, and volume control                      |
| Queue Management            | Manage the song queue and change the order                    |
| Key Change / Pitch Shifting | Adjust the pitch of songs                                     |
| File Management             | Advanced editing of downloaded file names                     |
| Admin Mode                  | Lock down features with admin mode                            |
| Headless Mode               | Run a dedicated pikaraoke server and stream to remote browser |

## Supported Devices / OS / Platforms

- Raspberry Pi
  - Requires a Raspberry Pi Model 3 or higher
  - Bookworm Desktop OS required for standalone/headed mode
  - For Pi 3: overclocking is recommended for smoother playback
- OSX
- Windows
- Linux

## Docker instructions

For Docker users, you can get going with one command. The deployed images includes everything you need to run in headless mode:

```sh
docker run vicwomg/pikaraoke:latest
```

For more information, [see official Dockerhub repo](https://hub.docker.com/repository/docker/vicwomg/pikaraoke)

## Native installation

### Install required programs

Pikaraoke requires Python 3.9 or greater. You can check your current version by running `python --version`.

[Python downloads](https://www.python.org/downloads/)

#### Raspberry Pi OS / Linux distros with `apt`:

```
sudo apt-get install ffmpeg -y
sudo apt-get install chromium-browser -y
sudo apt-get install chromium-chromedriver -y
```

Chromium/Chromdriver is optional if you're running with the `--headless` option.

#### Windows / OSX / Linux:

- FFmpeg 6.0 or greater: [FFmpeg downloads](https://ffmpeg.org/download.html)
- Chrome Browser: [Chrome](http://google.com/chrome) (only required for headed mode)

### Install pikaraoke via pip

Globally or within a virtual env:

```sh
# Install pikaraoke from PyPi
pip install pikaraoke
```

Note: if you did not use a venv, you may need to add the `--break-system-packages` parameter to ignore the warning and install pikaraoke and its dependencies globally. You may experience package conflicts if you have other python programs installed.

### Run

Pikaraoke is now installed in the `$PATH` with the command line interface `pikaraoke`. Start by calling the pikaraoke command.

```sh
# Run pikaraoke
pikaraoke
```

This will start pikaraoke in headed mode, and open Chrome browser with the splash screen. You can then connect to the QR code via your mobile device and start downloading and queueing songs.

Virtual env users: note that if you close your terminal between launches, you'll need to reactivate your venv before running pikaraoke.

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

## Developing pikaraoke

The Pikaraoke project utilizes Poetry for dependency management and local development.

- Install poetry: [Poetry](https://python-poetry.org/docs/#installation)
- Git clone this repo

From the pikaraoke directory:

```sh
# install dependencies
poetry install
```

```sh
# Run pikaraoke from the local codebase
poetry run pikaraoke
```

If you don't want to install poetry, you can alternately install pikaraoke directly from the source code root:

```sh
pip install .
```

See the [Pikaraoke development guide](https://github.com/vicwomg/pikaraoke/wiki/Pikaraoke-development-guide) for more details.

## Troubleshooting and guides

See the [TROUBLESHOOTING wiki](https://github.com/vicwomg/pikaraoke/wiki/FAQ-&-Troubleshooting) for help with issues.

There are also some great guides [on the wiki](https://github.com/vicwomg/pikaraoke/wiki/) to running pikaraoke in all manner of bizarre places including Android, Chromecast, and embedded TVs!
