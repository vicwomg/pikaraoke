# 🎤 Iribang - Riley's Korean-Optimized PiKaraoke 🎶

**Iribang** is an improved version of [PiKaraoke](https://github.com/vicwomg/pikaraoke), optimized for **better Korean language support** and overall **stability improvements**.

## 🚀 Improvements Over PiKaraoke
✅ **Better Korean Language Handling**  
   - Recognizes when Korean songs are requested and ensures proper playback.  

✅ **Enhanced FFmpeg Handling & Transcoding**  
   - No longer waits for the **entire video** to copy before playing.  
   - Uses **streaming of parts** for faster playback start times.  

✅ **More Stable Launching**  
   - Improved initialization and startup process.  

✅ **YouTube-DL Enhancements**  
   - Now **only downloads MP4 videos** (avoiding WebM for better stability across different platforms, especially microcomputers).  

---

## 📌 TODO (Upcoming Features)
🔹 **Improved Splash Screen Interaction**  
   - Current issue: Even with automated clicking, audio won’t play unless the confirm button is manually clicked.  

🔹 **UI Enhancements**  
   - Better **song list interface**.  
   - Improved **editing screen** for easier song management.  

---

## 🎵 Installation & Setup
_🚧 Coming Soon: Step-by-step installation guide for Raspberry Pi and other platforms._

### Requirements

- Python 3.10 or greater (You can check your current version by running `python --version`): [Python downloads](https://www.python.org/downloads/)
- FFmpeg: [FFmpeg downloads](https://ffmpeg.org/download.html)
- Chrome browser (recommended, though Safari and Firefox will work with the `--complete-transcode-before-play` option)
- A js runtime installed to your PATH (such as Node, Deno, Bun, QuickJS), this is a requirement as of yt-dlp 2025.11.12 otherwise some downloads may not work: https://github.com/yt-dlp/yt-dlp/wiki/EJS . Deno is probably easiest: https://deno.com/

#### Specific install instructions for Raspberry Pi OS / Linux distros with `apt`:

```
sudo apt-get install ffmpeg -y
sudo apt-get install chromium -y
sudo apt-get install chromium-driver -y
sudo curl -fsSL https://deno.land/x/install/install.sh | sh
```

Chromium/Chromdriver is optional if you're running with the `--headless` option.

#### Windows

You may want to try the install script by @lvmasterrj: https://github.com/lvmasterrj/win-pikaraoke-installer

### Install pikaraoke via pip

Globally or within a virtual env:

```sh
# Install pikaraoke from PyPi
pip install pikaraoke
```

Note: Some OS install `pip` as `pip3`. if you did not use a venv, you may need to add the `--break-system-packages` parameter to ignore the warning and install pikaraoke and its dependencies globally. You may experience package conflicts if you have other python programs installed.

### Run

Pikaraoke is now installed in the `$PATH` with the command line interface `pikaraoke`. Start by calling the pikaraoke command.

```sh
pikaraoke
```

This will start pikaraoke in headed mode, and open Chrome browser with the splash screen. You can then connect to the QR code via your mobile device and start downloading and queueing songs.

Virtual env users: note that if you close your terminal between launches, you'll need to reactivate your venv before running pikaraoke.

### Upgrading

To upgrade to the latest version of pikaraoke, run:

```sh
pip install pikaraoke --upgrade
```

### More Options

See the help command `pikaraoke --help` for available options.

## Docker instructions

For Docker users, you can get going with one command. The deployed images includes everything you need to run in headless mode:

```sh
docker run vicwomg/pikaraoke:latest
```

For more information, [see official Dockerhub repo](https://hub.docker.com/r/vicwomg/pikaraoke)

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
