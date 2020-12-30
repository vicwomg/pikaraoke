# PiKaraoke

PiKaraoke is a "KTV"-style karaoke song search and queueing system. It connects to your TV, and shows a QR code for computers and smartphones to connect to a web interface. From there, multiple users can seamlessly search your local track library, queue up songs, add an endless selection of new karaoke tracks from YouTube, and more. ~For use with Raspberry Pi devices.~ Works on Raspberry Pi, OSX, Windows, and Linux!

If you want to support this project with a little monetary tip, it's much appreciated:
<a href="https://www.buymeacoffee.com/vicwomg" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>

## What's new (January 2021)

- CDG file support! Also supports zipped cdg + mp3, just add the files to the root of the download directory (must be using vlc)
- Default download directories are now ~/pikaraoke-songs

## Features

- Web interface for multiple users to queue tracks
- Searching song library via autocomplete
- Adding new tracks from Youtube
- Offline storage of video files
- mp3 + cdg file support (vlc-only. Not supported on omxplayer, must be copied to download directory manually)
- Pause/Skip/Restart and volume control
- Now playing and Up Next display
- Basic editing of downloaded file names
- Queue editing
- Key Change / Pitch shifting (only available while using vlc)

## Screenshots

### TV

<p float="left">
  <img width="400" alt="pikaraoke-tv1" src="https://user-images.githubusercontent.com/4107190/95813571-06645600-0ccd-11eb-8341-021a20813990.png">
<img width="400" alt="pikaraoke-tv2" src="https://user-images.githubusercontent.com/4107190/95813564-019fa200-0ccd-11eb-95e1-57a002c357a3.png">
  </p>

### Web interface

<p float="left">
<img width="250" style="float:left" alt="pikaraoke-nowplaying" src="https://user-images.githubusercontent.com/4107190/95813193-2cd5c180-0ccc-11eb-89f4-11a69676dc6f.png">
<img width="250" style="float:left" alt="pikaraoke-queue" src="https://user-images.githubusercontent.com/4107190/95813195-2d6e5800-0ccc-11eb-8f00-1369350a8a1c.png">
<img width="250" style="float:left" alt="pikaraoke-browse" src="https://user-images.githubusercontent.com/4107190/95813182-27787700-0ccc-11eb-82c8-fde7f0a631c1.png">
<img width="250" style="float:left" alt="pikaraoke-search1" src="https://user-images.githubusercontent.com/4107190/95813197-2e06ee80-0ccc-11eb-9bf9-ddb24d988332.png">
<img width="250" style="float:left" alt="pikaraoke-search2" src="https://user-images.githubusercontent.com/4107190/95813190-2ba49480-0ccc-11eb-84e3-f902cbd489a2.png">
  </p>
  
### Old screens

https://imgur.com/a/wgBYeFb

## Supported Devices

This _should_ work on all raspberry pi devices, but multi-core models recommended. I did most development on a Pi Zero W and did as much optimization as I could handle, so it will work. However, certain things like concurrent downloads and browsing big song libraries will suffer. All this runs excellently on a Pi 3 and above.

Also works on macs, PCs, and linux!

## Installation

Install git, if you haven't already. (on raspberry pi: `sudo apt-get update; sudo apt-get install git`)
Install python3/pip3 (usually raspberry pis already have it, run `python3 --version` to check): https://www.python.org/downloads/ (python 2.7 may work, but is not officially supported)

Clone this repo:

```
git clone https://github.com/vicwomg/pikaraoke.git
cd pikaraoke
```

### Raspberry pi

Run the setup script:

```
./setup-pi.sh
```

You will then probably need to reboot since this changes a boot setting (gpu_mem=128). This is to prevent certain videos from showing visual artifacts (green pixel distortion)

```
sudo reboot
```

### Linux / OSX

- Install VLC (to its default location): https://www.videolan.org/
- Install ffmpeg (only if you want to use --high-quality flag) https://ffmpeg.org/download.html

Install requirements from the pikaraoke directory:

```
pip3 install -r requirements.txt
pip3 install --upgrade youtube_dl
```

### Windows

- Install VLC (to its default location): https://www.videolan.org/
- Install ffmpeg (only if you want to use --high-quality flag) https://ffmpeg.org/download.html
- Install MS Visual C++ (required to launch youtube-dl)  https://www.microsoft.com/en-US/download/details.aspx?id=5555
- Install youtube-dl.exe. FYI, pip3 didn't seem to work for this on windows, so I used scoop as a package manager and I think it handles filed permissions best. Install scoop by following the instructions here: https://scoop.sh/

```
scoop install youtube-dl
```

Open a powershell, and go to the pikaraoke directory:

```
pip3 install -r requirements.txt
```

Note: if you have trouble installing pygame, there's apparently an incompatibility with Python 3.8. Try upgrading to the latest python version or downgrading to 3.7.

## Launch

cd to the pikaraoke directory and run:

`sudo python3 app.py` (pi devices) or `python3 app.py` (other)

You must run as sudo on pi devices if you are running directly from the console since PiKaraoke uses pygame to control the screen buffer. You can probably run as non-sudo from the Raspbian desktop environment, but may need to specify a different download directory than the default with the -d option.

The app should launch and show the PiKaraoke splash screen and a QR code and a URL. Using a device connected to the same wifi network as the Pi, scan this QR code or enter the URL into a browser. You are now connected! You can start exploring the UI and adding/queuing new songs directly from YouTube.

## Auto-start PiKaraoke

This is optional, but you may want to make your raspberry pi a dedicated karaoke device. If so, add the following to your /etc/rc.local file (paths and arguments may vary) to always start pikaraoke on reboot.

```
# start pikaraoke on startup
/usr/bin/python3 /home/pi/pikaraoke/app.py &
```

Or if you're like me and want some logging for aiding debugging, the following stores output at: /var/log/pikaraoke.log:

```
# start pikaraoke on startup / logging
/usr/bin/python3 /home/pi/pikaraoke/app.py >> /var/log/pikaraoke.log 2>&1 &
```

If you want to kill the pikaraoke process, you can do so from the PiKaraoke Web UI under: `Info > Quit pikaraoke`. Or you can ssh in and run `sudo killall python` or something similar.

Note that if your wifi/network is inactive pikaraoke will error out 10 seconds after being launched. This is to prevent the app from hijacking your ability to login to repair the connection.

## Usage

Here is the full list of command line arguments:

```
usage: app.py [-h] [-p PORT] [-d DOWNLOAD_PATH] [-o OMXPLAYER_PATH]
              [-y YOUTUBEDL_PATH] [-v VOLUME] [-s SPLASH_DELAY] [-l LOG_LEVEL]
              [--show-overlay] [--hide-ip] [--hide-splash-screen] [--adev ADEV]
              [--dual-screen] [--high-quality] [--use-vlc] [--vlc-path VLC_PATH] 
              [--vlc-port VLC_PORT]

optional arguments:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  Desired http port (default: 5000)
  -d DOWNLOAD_PATH, --download-path DOWNLOAD_PATH
                        Desired path for downloaded songs. (default:
                        ~/pikaraoke/songs)
  -o OMXPLAYER_PATH, --omxplayer-path OMXPLAYER_PATH
                        Path of omxplayer. Only important to raspberry pi
                        hardware. (default: /usr/bin/omxplayer)
  -y YOUTUBEDL_PATH, --youtubedl-path YOUTUBEDL_PATH
                        Path of youtube-dl. (default: /usr/local/bin/youtube-
                        dl)
  -v VOLUME, --volume VOLUME
                        If using omxplayer, the initial player volume is
                        specified in millibels. Negative values ok. (default:
                        0 , Note: 100 millibels = 1 decibel).
  -s SPLASH_DELAY, --splash-delay SPLASH_DELAY
                        Delay during splash screen between songs (in secs).
                        (default: 5 )
  -l LOG_LEVEL, --log-level LOG_LEVEL
                        Logging level int value (DEBUG: 10, INFO: 20, WARNING:
                        30, ERROR: 40, CRITICAL: 50). (default: 20 )
  --show-overlay        Show text overlay in omxplayer with song title and IP.
                        (feature is broken on Pi 4 omxplayer 12/24/2019)
  --hide-ip             Hide IP address from the screen.
  --hide-splash-screen  Hide splash screen before/between songs.
  --adev ADEV           Pass the audio output device argument to omxplayer.
                        Possible values: hdmi/local/both/alsa[:device]. If you
                        are using a rpi USB soundcard or Hifi audio hat, try:
                        'alsa:hw:0,0' Default 'both'
  --dual-screen         Output video to both HDMI ports (raspberry pi 4 only)
  --high-quality        Download higher quality video. Note: requires ffmpeg
                        and may cause CPU, download speed, and other
                        performance issues
  --use-vlc             Use VLC Player instead of the default OMX Player.
                        Enabled by default on non-pi hardware. Note: if you
                        want to play audio to the headphone jack on a rpi,
                        you'll need to configure this in raspi-config:
                        'Advanced Options > Audio > Force 3.5mm (headphone)'
  --vlc-path VLC_PATH   Full path to VLC (Defaults to standard installation
                        location)
  --vlc-port VLC_PORT   HTTP port for VLC remote control api (Default: 5002)
```

## Screen UI

Upon launch, the connected monitor/TV should show a splash screen with the IP of PiKaraoke along with a QR code.

If there's a keyboard attached, you can exit pikaraoke by pressing "esc". You can toggle fullscreen mode by pressing "f"

Make sure you are connected to the same network/wifi. You can then enter the shown IP or scan the QR code on your smartphone/tablet/computer to open it in a browser. From there you should see the PiKaraoke web interface. It is hopefully pretty self-explanatory, but if you really need some help:

## Web UI

### Home

- View Now Playing and Next tracks
- Access controls to repeat, pause, skip and control volume 
- (only when --use-vlc option is used) Transpose slider to change playback pitch

### Queue

- Edit the queue/playlist order (up and down arrow icons)
- Delete from queue ( x icon )
- Add random songs to the queue
- Clear the queue

### Songs

- Add songs to the queue by searching current library on local storage (likely empty at first), search is executed autocomplete-style
- Add new songs from the internet by using the second search box
- Click browse to view the full library. From here you can edit files in the library (rename/delete).

### Info

- Shows the IP and QR code to share with others
- Shows CPU / Memory / Disk Use stats
- Allows user to quit to console, shut down, or reboot system. Always shut down from here before you pull the plug on pikaraoke!

## Troubleshooting

### I'm not hearing audio out of the headphone jack

By default the raspbian outputs to HDMI audio when it's available. Pikaraoke tries to output to both HDMI and headphone, but if it doesn't work you may need to to force it to the headphone jack. This is definitely the case when using VLC. To do so, change following setting on the pi:
`sudo raspi-config`
Advanced Options > Audio > Force 3.5mm (headphone)

See: https://www.raspberrypi.org/documentation/configuration/audio-config.md

### I'm having audio issues with the headphone jack, external sound card, or other audio device

Omxplayer tends to have some inconsistent results across different hardware combinations. Try experimenting with the --adev option, which specifies the audio device to omxplayer. Defaults to 'both' which is hdmi and headphone out. Other possible values are: hdmi/local/both/alsa[:device].

If you're hearing distorted audio out, try '--adev alsa'

If you're using an external USB sound card or hifi audio hat like the hifiberry, you'll need to add the argument '--adev alsa:hw:0,0' when you launch pikaraoke

You can also try vlc with the --use-vlc option. There have been reports that HDMI audio works fine with vlc, but to use the headphone jack you need to edit some also conf files:

`sudo nano /usr/share/alsa/alsa.conf`

Scroll down and change defaults.ctl.card and defaults.pcm.card to "1"

```
defaults.ctl.card 1
defaults.pcm.card 1
```

Note this value might be diffent in older versions of Raspbian. See source article for details: https://raspberrypi.stackexchange.com/a/39942

### Songs aren't downloading!

Make sure youtube-dl is up to date, old versions have higher failure rates due to security changes in Youtube. You can see your current version installed by navigating to `Info > System Info > Youtube-dl version`. The version number is usually the date it was released. If this is older than a few months, chances are it will need an update.

You can update youtube-dl directly from the web UI. Go to `Info > Update Youtube-dl` (depending on how you installed, you may need to be running pikaraoke as sudo for this to work)

Or, from the CLI (path may vary):
`youtube-dl -U`

### Downloads are slow!

youtube-dl is very CPU intensive, especially for single-core devices like the pi models zero and less-than 2. The more simultaneous downloads there are, the longer they will take. Try to limit it to 1-2 at a time. Pi 3 can handle quite a bit more.

### I brought my pikaraoke to a friend's house and it can't connect to their network. How do I change wifi connection without ssh?

These are my preferred ways to do it, but they might require either a USB keyboard or a computer with an SD Card reader.

- _Completely Headless_: I can highly recommend this package: https://github.com/jasbur/RaspiWiFi . Install it according to the directions and it will detect when there is no network connection and act as a Wifi AP allowing you to configure the wifi connection from your smartphone, similar to a Chromecast initial setup. You can even wire up a button to GPIO18 and 3.3V to have a manual wifi reset button. This, along with auto-launch in rc.local makes PiKaraoke a standalone appliance!
- _USB Keyboard_: plug in a USB keyboard to the pi. After it boots up, log in and run "sudo raspi-config" and configure wifi through the Network Options section. If the desktop UI is installed, you can also run "startx" and configure wifi from the Raspbian GUI. You can also manually edit /etc/wpa_supplicant/wpa_supplicant.conf as desribed below.
- _SD Card Reader_: Remove the pi's SD card and open it on a computer with an SD card reader. It should mount as a disk drive. On the BOOT partition, add a plaintext file named "wpa_supplicant.conf" and put the following in it:

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

### Can I run PiKaraoke without a wifi/network connection?

Actually, yes! But you can only access your existing library and won't be able to download new songs, obviously.

If you run your pi as a wifi access point, your browser can connect to that access point, and it should work. See: https://www.raspberrypi.org/documentation/configuration/wireless/access-point.md

Or an even easier approach, if you install this: https://github.com/jasbur/RaspiWiFi (used for configuring wifi connections headless, see above). While it's in AP mode, you can connect to the pi as an AP and connect directly to it at http://10.0.0.1:5000

### Where do I plug in a microphone?

Ideally, you'd have a mixer and amplifier that you could run the line out of the pi to, as well as the microphones. I used this affordable wireless microphone set from amazon: https://www.amazon.com/gp/product/B01N6448Q4/ It has a line in so you can also run PiKaraoke into the mix, and output to an amplifier.

The pi doesn't have a hardware audio input. Technically, you should be able to run a microphone through it with a USB sound card attached to the pi, but I personally wouldn't bother due to latency and quality issues.

### How do I change song pitch/key?

First of all, you must be running pikaraoke with the --use-vlc option.

While a song is playing, the home screen of the web interface will show a transpose slider. Slide it up or down based on your preference and press the "ok" button to restart the song in the given key.

### How do I add cdg or mp3+cdg zip files?

You'll need to add them manually by copying them to the root of your download folder. Run `python app.py --help` and look under DOWNLOAD_PATH to find out what the default folder is, or specify your own. Only cdg/mp3 pairs and .zip files are supported.

### My mp3/cdg file is not playing

CDG files must have an mp3 file with a exact matching file name. They can also be bundled together in a single zip file, but the filenames in the zip must still match. They must also be placed in the root of the download directory and not stashed away in sub-directories. Also, if you're running omxplayer instead of vlc, there will only be audio.

### I'm on a laptop, how do I output just pikaraoke to an external monitor/screen?

You might be able to just drag the windows to the target screen (press 'f' to toggle fullscreen). But in my experience there can be issues figuring out which monitor to use once videos start playing. For now you'd probably have the most consistent experience using single-screen mirrored mode.
