# PiKaraoke

Karaoke song search and queueing system that uses Youtube videos as a source. For use with Raspberry Pi devices.

## Features

*  Web interface for multiple users to queue tracks
*  Searching song library via autocomplete
*  Adding new tracks from Youtube
*  Offline storage of video files
*  Pause/Skip/Restart and volume control
*  Now playing and Up Next display
*  Basic editing of downloaded file names
*  Queue editing

## Screenshots

https://imgur.com/a/wgBYeFb

## Supported Devices

This *should* work on all raspberry pi devices, but multi-core models recommended. I did most development on a Pi Zero W and did as much optimization as I could handle, so it will work. However, certain things like concurrent downloads and browsing big song libraries will suffer. All this runs excellently on a Pi 3.

## Setup

Assumes a fresh Raspbian install.

Install required binaries:
```
sudo apt-get update
sudo apt-get install git libjpeg-dev omxplayer python-pip python-pygame python-lxml -y
sudo pip install --upgrade youtube_dl
```

Notes on above: Do NOT use apt-get to install youtube-dl, it is an outdated version and puts it in a different directory than expected. And conversely, don't use pip to install pygame, and lxml, when I tried on a fresh raspbian lite image I got dependency errors with pip.

Clone this repo. The following puts it in /home/pi:
```
cd ~
git clone https://github.com/vicwomg/pikaraoke.git
```

Install the remaining python dependencies (these instructions install packages globally, use virtualenv if you prefer):

```
cd pikaraoke
sudo pip install -r requirements.txt
```

Finally, bump up your GPU memory or some videos will show a GSOD (green screen of death).

`sudo nano /boot/config.txt`

Add the following line:

`gpu_mem=128`

## Launch

cd to the pikaraoke directory and run:

`sudo python app.py`

Yes, you must run as sudo since pikaraoke uses pygame to control the screen buffer.

By default, the http port is 5000 and it downloads songs to "/usr/lib/pikaraoke/songs". To change this, you can supply the following command line arguments (example):

`sudo python app.py --port 8080 --download-path /home/pi/songs`

## Auto-start PiKaraoke

This is optional, but you may want to make your pi a dedicated karaoke device. If so, add the following to your /etc/rc.local file (paths and arguments may vary) to always start pikaraoke on reboot.

```
# start pikaraoke on startup
/usr/bin/python /home/pi/pikaraoke/app.py &
```

Or if you're like me and want some logging for aiding debugging, the following stores output at: /var/log/pikaraoke.log:

```
# start pikaraoke on startup / logging
/usr/bin/python /home/pi/pikaraoke/app.py >> /var/log/pikaraoke.log 2>&1 &
```

If you want to kill the pikaraoke process, you can do so from the PiKaraoke Web UI under: `Info > Quit to console`. Or you can ssh in and run `sudo killall python` or something similar.

Note that if your wifi/network is inactive pikaraoke will error out 10 seconds after being launched. This is to prevent the app from hijacking your ability to login to repair the connection. 

## Usage

Here is the full list of command line arguments:

```
usage: app.py [-h] [-p PORT] [-d DOWNLOAD_PATH] [-o OMXPLAYER_PATH]
              [-y YOUTUBEDL_PATH] [-v VOLUME] [-s SPLASH_DELAY] [-l LOG_LEVEL]
              [--hide-overlay] [--hide-ip] [--hide-splash-screen]

optional arguments:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  Desired http port (default: 5000)
  -d DOWNLOAD_PATH, --download-path DOWNLOAD_PATH
                        Desired path for downloaded songs. (default:
                        /usr/lib/pikaraoke/songs)
  -o OMXPLAYER_PATH, --omxplayer-path OMXPLAYER_PATH
                        Path of omxplayer. (default: /usr/bin/omxplayer)
  -y YOUTUBEDL_PATH, --youtubedl-path YOUTUBEDL_PATH
                        Path of youtube-dl. (default: /usr/local/bin/youtube-
                        dl)
  -v VOLUME, --volume VOLUME
                        Initial player volume in millibels. Negative values
                        ok. (default: 0 , Note: 100 millibels = 1 decibel)
  -s SPLASH_DELAY, --splash-delay SPLASH_DELAY
                        Delay during splash screen between songs (in secs).
                        (default: 4 )
  -l LOG_LEVEL, --log-level LOG_LEVEL
                        Logging level int value (DEBUG: 10, INFO: 20, WARNING:
                        30, ERROR: 40, CRITICAL: 50). (default: 20 )
  --show-overlay        Show overlay in omxplayer with song title and IP.
                        (feature is broken on Pi 4 omxplayer 12/24/2019)
  --hide-ip             Hide IP address from the screen.
  --hide-splash-screen  Hide splash screen before/between songs.
```

## Screen UI

Upon launch, the connected monitor/TV should show a splash screen with the IP of PiKaraoke along with a QR code.

Make sure you are connected to the same network/wifi. You can then enter the shown IP or scan the QR code on your smartphone/tablet/computer to open it in a browser. From there you should see the PiKaraoke web interface. It is hopefully pretty self-explanitory, but if you really need some help:

## Web UI

### Home (Microphone Icon)

*  View Now Playing and Next tracks
*  Access controls to repeat, pause, skip and control volume

### Queue

*  Edit the queue/playlist order (up and down arrow icons)
*  Delete from queue ( x icon )
*  Add random songs to the queue
*  Clear the queue

### Songs

*  Add songs to the queue by searching current library on local storage (likely empty at first), search is executed autocomplete-style
*  Add new songs from the internet by using the second search box
*  Click browse to view the full library. From here you can edit files in the library (rename/delete).

### Info

*  Shows the IP and QR code to share with others
*  Shows CPU / Memory / Disk Use stats
*  Allows user to quit to console, shut down, or reboot system. Always shut down from here before you pull the plug on pikaraoke!

## Troubleshooting

### Songs aren't downloading!

Make sure youtube-dl is up to date, old versions have higher failure rates due to security changes in Youtube. You can see your current version installed by navigating to `Info > System Info > Youtube-dl version`. The version number is usually the date it was released. If this is older than a few months, chances are it will need an update.

You can update youtube-dl directly from the web UI. Go to `Info > Update Youtube-dl`

Or, from the CLI:
`sudo pip install --upgrade youtube_dl`

### Downloads are slow!

youtube-dl is very CPU intensive, especially for single-core devices like the pi models zero and less-than 2. The more simultaneous downloads there are, the longer they will take. Try to limit it to 1-2 at a time. Pi 3 can handle quite a bit more.

### I brought my pikaraoke to a friend's house and it can't connect to their network. How do I change wifi connection without ssh?

These are my preferred ways to do it, but they might require either a USB keyboard or a computer with an SD Card reader.

* _Completely Headless_: I can highly recommend this package: https://github.com/jasbur/RaspiWiFi . Install it according to the directions and it will detect when there is no network connection and act as a Wifi AP allowing you to configure the wifi connection from your smartphone, similar to a Chromecast initial setup. You can even wire up a button to GPIO18 and 3.3V to have a manual wifi reset button. This, along with auto-launch in rc.local makes PiKaraoke a standalone appliance!
* _USB Keyboard_: plug in a USB keyboard to the pi. After it boots up, log in and run "sudo raspi-config" and configure wifi through the Network Options section. If the desktop UI is installed, you can also run "startx" and configure wifi from the Raspbian GUI. You can also manually edit /etc/wpa_supplicant/wpa_supplicant.conf as desribed below.
* _SD Card Reader_: Remove the pi's SD card and open it on a computer with an SD card reader. It should mount as a disk drive. On the BOOT partition, add a plaintext file named "wpa_supplicant.conf" and put the following in it:

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

Ideally, you'd have a mixer and amplifier that you could run the line out of the pi to, as well as the microphones.

The pi doesn't have a hardware audio input. Technically, you should be able to run a microphone through it with a USB sound card attached to the pi, but I personally wouldn't bother due to latency and quality issues.

Ideally, you'd have a mixer and amplifier that you could run the line out of the pi to, as well as the microphones. I used this affordable wireless microphone set from amazon: https://www.amazon.com/gp/product/B01N6448Q4/ It has a line in so you can also run PiKaraoke into the mix, and output to an amplifier.

### How do I change song pitch/key?

This is currently not supported due to lack of know-how. As far as I can tell we'd have to pipe omxplayer into some realtime-yet-lightweight audio DSP. Let me know if you have ideas on how to implement it.

In the meantime, you might be able to get away with running the line out through a pitch shift guitar effects pedal or similar device.
