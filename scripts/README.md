## Installation

### General dependencies installation

Install git, if you haven't already.
(on raspberry pi: `sudo apt-get update; sudo apt-get install git`)

Install python3/pip3
(usually raspberry pi OS already has it, run `python3 --version` to check): https://www.python.org/downloads/
Python >= 3.8 is necessary

Clone this repo:

```
git clone https://github.com/vicwomg/pikaraoke.git
cd pikaraoke
```

If you plan to run the splash screen in auto-launch headed mode, you also need to install Chrome browser. On raspberry pi, Chromium should be installed already, which also works fine.

### Raspberry pi / Linux / OSX

Run the setup script to install dependencies and set up the python env:

```
./setup.sh
```

If you're on a raspberry pi or debian system the setup script should have handled installing ffmpeg via apt.

If you're on OSX or another Linux distro, manually install the latest stable version FFMPEG 6.0 or greater from here: https://ffmpeg.org/download.html . Do not install experimental snapshot builds.

On Ubuntu, apt seemed to keep installing an old 4.X version of ffmpeg. I found better luck grabbing a pre-built version of ffmpeg 6.0+ and manually copying it to /usr/bin/. Pre-built releases were obtained from this repo: https://github.com/BtbN/FFmpeg-Builds/releases

### Windows

Manually install ffmpeg 6.0 or greater https://ffmpeg.org/download.html

Run the setup script to install python dependencies:

```
setup-windows.bat
```

Windows firewall may initially block connections to port 5555 and 5556. Be sure to allow these. It should prompt the first time you run pikaraoke and launch a song. Otherwise, configure it manually in the security settings.

## Launch

cd to the pikaraoke directory and run:

`./pikaraoke.sh` (linux/osx/pi) or `pikaraoke.bat` (windows)

The app should launch and show the PiKaraoke splash screen and a QR code and a URL. Using a device connected to the same wifi network as the Pi, scan this QR code or enter the URL into a browser. You are now connected! You can start exploring the UI and adding/queuing new songs directly from YouTube.

If you'd like to manually open the splash screen/player or open it on a separate computer's web browser, run `./pikaraoke.sh --headless` to suppress the launch of the splash screen. Then point your browser the the URL it tells you.

For more options, run `./pikaraoke.sh --help`

## Auto-start PiKaraoke

This is optional, but you may want to make your raspberry pi a dedicated karaoke device.

```
mkdir /home/pi/.config/autostart
nano /home/pi/.config/autostart/pikaraoke.desktop
```

Add this to the file, assuming you installed to /home/pi/pikaraoke, change the Exec path accordingly if not

```
[Desktop Entry]
Type=Application
Name=Pikaraoke
Exec=/home/pi/pikaraoke/pikaraoke.sh
```

Restart and it should auto-launch on your next boot.

If you want to kill the pikaraoke process, you can do so from the PiKaraoke Web UI under: `Info > Quit pikaraoke`. Or you can ssh in and run `sudo killall python` or something similar.

Note that if your wifi/network is inactive pikaraoke will error out 10 seconds after being launched. This is to prevent the app from hijacking your ability to login to repair the connection.

## Usage

May not be up to date, run `python3 app.py --help` for the latest:

```
usage: app.py [-h] [-p PORT] [-f FFMPEG_PORT] [-d DOWNLOAD_PATH] [-y YOUTUBEDL_PATH] [-v VOLUME] [-s SPLASH_DELAY] [-t SCREENSAVER_TIMEOUT]
              [-l LOG_LEVEL] [--hide-url] [--prefer-ip] [--hide-raspiwifi-instructions] [--hide-splash-screen] [--dual-screen] [--high-quality]
              [--logo-path LOGO_PATH] [-u URL] [--hide-overlay] [--admin-password ADMIN_PASSWORD] [--window-size WIDTH,HEIGHT]

options:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  Desired http port (default: 5555)
  -f FFMPEG_PORT, --ffmpeg-port FFMPEG_PORT
                        Desired ffmpeg port. This is where video stream URLs will be pointed (default: 5556)
  -d DOWNLOAD_PATH, --download-path DOWNLOAD_PATH
                        Desired path for downloaded songs. (default: ~/pikaraoke-songs)
  -y YOUTUBEDL_PATH, --youtubedl-path YOUTUBEDL_PATH
                        Path of youtube-dl. (default: /Users/vic/coding/pikaraoke/.venv/bin/yt-dlp)
  -v VOLUME, --volume VOLUME
                        Set initial player volume. A value between 0 and 1. (default: 0.85)
  -s SPLASH_DELAY, --splash-delay SPLASH_DELAY
                        Delay during splash screen between songs (in secs). (default: 3 )
  -t SCREENSAVER_TIMEOUT, --screensaver-timeout SCREENSAVER_TIMEOUT
                        Delay before the screensaver begins (in secs). (default: 300 )
  -l LOG_LEVEL, --log-level LOG_LEVEL
                        Logging level int value (DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50). (default: 20 )
  --hide-url            Hide URL and QR code from the splash screen.
  --prefer-hostname     Use the local hostname instead of the IP as the connection URL. Use at your discretion: mDNS is not guaranteed to work on all
                        LAN configurations. Defaults to False
  --hide-raspiwifi-instructions
                        Hide RaspiWiFi setup instructions from the splash screen.
  --hide-splash-screen, --headless
                        Headless mode. Don't launch the splash screen/player on the pikaraoke server
  --high-quality        Download higher quality video. Note: requires ffmpeg and may cause CPU, download speed, and other performance issues
  --logo-path LOGO_PATH
                        Path to a custom logo image file for the splash screen. Recommended dimensions ~ 2048x1024px
  -u URL, --url URL     Override the displayed IP address with a supplied URL. This argument should include port, if necessary
  --hide-overlay        Hide overlay that shows on top of video with pikaraoke QR code and IP
  --admin-password ADMIN_PASSWORD
                        Administrator password, for locking down certain features of the web UI such as queue editing, player controls, song editing,
                        and system shutdown. If unspecified, everyone is an admin.
  --window-size WIDTH,HEIGHT
                        Explicitly set the width and height of the splash screen, where the WIDTH and HEIGHT values are specified in pixels.
```
