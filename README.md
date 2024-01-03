# PiKaraoke

PiKaraoke is a "KTV"-style karaoke song search and queueing system. It connects to your TV, and shows a QR code for computers and smartphones to connect to a web interface. From there, multiple users can seamlessly search your local track library, queue up songs, add an endless selection of new karaoke tracks from YouTube, and more. Works on Raspberry Pi, OSX, Windows, and Linux!

If you want to support this project with a little monetary tip, it's much appreciated: <br/>
<a href="https://www.buymeacoffee.com/vicwomg" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>

## What's new (1.2)

The player and splash screen is now HTML-based

Why? Less pesky dependencies for one. Pygame was previously used to render the splash screen and VLC would pop on top of it. Both these packages proved to be difficult to maintain on Raspberry Pi OS versions. This has been replaced with a browser-based renderer which will host both the splash screen and video playback (streamed via ffmpeg) which should work much better on a wide variety of OS. Secondly, this means standalone server support: now you can run pikaraoke as a dedicated server process, launch the splash screen on a remote browser, and don't have to have your pi connected to the TV!

- Splash screen player is way more dynamic feature-rich now
- Better python environment handling and yt-dlp install isolation
- Lots of under-the-hood bugfixes and improvements from the backlog
- Sunfly-inspired singing dolphin logo and screensaver :)

## Features

- Web interface for multiple users to queue tracks
- Player/splash screen with connection QR code and "Next up" display
- Searching/browsing a local song library
- Adding new songs from Youtube
- mp3 + cdg support, including compressed .zip bundles
- Pause/Skip/Restart and volume control
- Advanced editing of downloaded file names
- Queue management
- Key Change / Pitch shifting
- Lock down features with admin mode

## Screenshots

### TV

<p float="left">
  <img width="400" alt="pikaraoke-tv1" src="https://user-images.githubusercontent.com/4107190/95813571-06645600-0ccd-11eb-8341-021a20813990.png">
<img width="400" alt="pikaraoke-tv2" src="https://user-images.githubusercontent.com/4107190/95813564-019fa200-0ccd-11eb-95e1-57a002c357a3.png">
  </p>

### Web interface

<div style="display: flex">
<img width="250" alt="pikaraoke-nowplaying" src="https://user-images.githubusercontent.com/4107190/95813193-2cd5c180-0ccc-11eb-89f4-11a69676dc6f.png">
<img width="250" alt="pikaraoke-queue" src="https://user-images.githubusercontent.com/4107190/95813195-2d6e5800-0ccc-11eb-8f00-1369350a8a1c.png">
<img width="250"  alt="pikaraoke-browse" src="https://user-images.githubusercontent.com/4107190/95813182-27787700-0ccc-11eb-82c8-fde7f0a631c1.png">
<img width="250"  alt="pikaraoke-search1" src="https://user-images.githubusercontent.com/4107190/95813197-2e06ee80-0ccc-11eb-9bf9-ddb24d988332.png">
<img width="250"  alt="pikaraoke-search2" src="https://user-images.githubusercontent.com/4107190/95813190-2ba49480-0ccc-11eb-84e3-f902cbd489a2.png">
</div>

## Supported Devices / OS

Raspberry Pi 3 and above. Anything else will likely be too slow.

Other pi considerations:

- Should be running Raspberry pi desktop OS if running headed, since it requires a browser
- 32-bit version of the OS is recommended. 64-bit seemed slower in my testing, but pi4 and above can probably handle it.
- Disable "screen blanking" in raspi-config if you want to prevent the display from turning off when idle
- Pi3 might struggle a bit with high-res video playback. Overclocking seems to help

Works fine on modern Mac, PCs, and Linux!

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

If you're on OSX or another Linux distro, manually install FFMPEG from here: https://ffmpeg.org/download.html

### Windows

Manually install ffmpeg https://ffmpeg.org/download.html

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
              [--logo-path LOGO_PATH] [-u URL] [--hide-overlay] [--admin-password ADMIN_PASSWORD]

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
  --prefer-ip           Show the IP instead of the fully qualified local domain name. Default: False
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
```

## Troubleshooting

### I'm not hearing audio out of the headphone jack

By default the raspbian outputs to HDMI audio when it's available. Pikaraoke tries to output to both HDMI and headphone, but if it doesn't work you may need to to force it to the headphone jack. This is definitely the case when using VLC. To do so, change following setting on the pi:
`sudo raspi-config`
Advanced Options > Audio > Force 3.5mm (headphone)

See: https://www.raspberrypi.org/documentation/configuration/audio-config.md

If you're still having issues with hearing audio, it has been reported this helps on raspberry pi 4 devices:

`sudo nano /usr/share/alsa/alsa.conf`

Scroll down and change defaults.ctl.card and defaults.pcm.card to "1"

```
defaults.ctl.card 1
defaults.pcm.card 1
```

Note this value might be different in older versions of Raspbian or if you have external audio hardware. See source article for details: https://raspberrypi.stackexchange.com/a/39942

### Songs aren't downloading!

Make sure youtube-dl is up to date, old versions have higher failure rates due to security changes in Youtube. You can see your current version installed by navigating to `Info > System Info > Youtube-dl version`. The version number is usually the date it was released. If this is older than a couple of months, chances are it will need an update.

You can update youtube-dl directly from the web UI. Go to `Info > Update Youtube-dl`

### Downloads are slow!

youtube-dl is very CPU intensive, especially for single-core devices like the pi models zero and less-than 2. The more simultaneous downloads there are, the longer they will take. Try to limit it to 1-2 at a time. Pi 3 can handle quite a bit more.

### I brought my pikaraoke to a friend's house and it can't connect to their network. How do I change wifi connection without ssh?

These are my preferred ways to do it, but they might require either a USB keyboard or a computer with an SD Card reader.

- _USB Keyboard_: plug in a USB keyboard to the pi. After it boots up, log in and run "sudo raspi-config" and configure wifi through the Network Options section. If the desktop UI is installed, you can also run "startx" and configure wifi from the Raspbian GUI. You can also manually edit /etc/wpa_supplicant/wpa_supplicant.conf as desribed below.
- _SD Card Reader_: Remove the pi's SD card and open it on another computer with an SD card reader. It should mount as a disk drive. On the BOOT partition, add a plaintext file named "wpa_supplicant.conf" and put the following in it:

```
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=<Your 2-letter country code, ex. US>
network={
  ssid="<the wifi ap ssid name>"
  psk="<the wifi password>"
  key_mgmt=WPA-PSK
}
```

Add the SD card back to the pi and start it up. On boot, Raspbian should automatically add the wpa_supplicant.conf file to the correct location and connect to wifi.

Finally, this package can set up your pi as a self-configuring wireless access point, but hasn't been updated in a while https://github.com/jasbur/RaspiWiFi

### Can I run PiKaraoke without a wifi/network connection?

Yes, but you can only access your existing library and won't be able to download new songs.

If you run your pi as a wifi access point, your browser can connect to that access point, and it should work. See: https://www.raspberrypi.org/documentation/configuration/wireless/access-point.md

You can also try this: https://github.com/jasbur/RaspiWiFi (used for configuring wifi connections headless). While it's in AP mode, you can connect to the pi as an AP and connect directly to it at http://10.0.0.1:5555

### Where do I plug in a microphone?

The pi doesn't have a hardware audio input. Technically, you should be able to run a microphone through it with a USB sound card attached to the pi (or USB microphone), but the latency is generally not usable.

Ideally, you'd have a mixer and amplifier that you could run the line out of the pi to, as well as the microphones. I used this affordable wireless microphone set from amazon: https://amzn.to/2OXKXdc (affiliate link) It has a line-in so you can also run PiKaraoke into the mix, and output to an amplifier.

### How do I change song pitch/key?

While a song is playing, the home screen of the web interface will show a transpose slider. Slide it up or down based on your preference and press the "ok" button to restart the song in the given key.

### How do I add cdg or mp3+cdg zip files?

You'll need to add them manually by copying them to the root of your download folder. Run `pikaraoke.sh --help` and look under DOWNLOAD_PATH to find out what the default folder is, or specify your own. Only cdg/mp3 pairs and .zip files are supported.

### My mp3/cdg file is not playing

CDG files must have an mp3 file with a exact matching file name. They can also be bundled together in a single zip file, but the filenames in the zip must still match. They must also be placed in the root of the download directory and not stashed away in sub-directories.
