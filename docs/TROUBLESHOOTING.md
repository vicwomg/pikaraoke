## Troubleshooting

Note that much of this info is relevant only to Raspberry Pi devices.

### How do I update pikaraoke to the latest version?

`pip install --upgrade pikaraoke`

### How do I display all the command line options?

`pikaraoke --help`

Here is a snapshot (may not be up to date):

```
usage: pikaraoke [-h] [-p PORT] [--window-size WINDOW_SIZE] [-f FFMPEG_PORT] [-d DOWNLOAD_PATH [DOWNLOAD_PATH ...]]
                 [-y YOUTUBEDL_PATH [YOUTUBEDL_PATH ...]] [-v VOLUME] [-n] [-s SPLASH_DELAY] [-t SCREENSAVER_TIMEOUT] [-l LOG_LEVEL]
                 [--hide-url] [--prefer-hostname] [--hide-raspiwifi-instructions] [--hide-splash-screen] [--high-quality]
                 [--logo-path LOGO_PATH [LOGO_PATH ...]] [-u URL] [-m FFMPEG_URL] [--hide-overlay] [--admin-password ADMIN_PASSWORD]

options:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  Desired http port (default: 5555)
  --window-size WINDOW_SIZE
                        Desired window geometry in pixels, specified as width,height
  -f FFMPEG_PORT, --ffmpeg-port FFMPEG_PORT
                        Desired ffmpeg port. This is where video stream URLs will be pointed (default: 5556)
  -d DOWNLOAD_PATH [DOWNLOAD_PATH ...], --download-path DOWNLOAD_PATH [DOWNLOAD_PATH ...]
                        Desired path for downloaded songs. (default: ~/pikaraoke-songs)
  -y YOUTUBEDL_PATH [YOUTUBEDL_PATH ...], --youtubedl-path YOUTUBEDL_PATH [YOUTUBEDL_PATH ...]
                        Path of youtube-dl. (default: yt-dlp)
  -v VOLUME, --volume VOLUME
                        Set initial player volume. A value between 0 and 1. (default: 0.85)
  -n, --normalize-audio
                        Normalize volume. May cause performance issues on slower devices (default: False)
  -s SPLASH_DELAY, --splash-delay SPLASH_DELAY
                        Delay during splash screen between songs (in secs). (default: 3 )
  -t SCREENSAVER_TIMEOUT, --screensaver-timeout SCREENSAVER_TIMEOUT
                        Delay before the screensaver begins (in secs). (default: 300 )
  -l LOG_LEVEL, --log-level LOG_LEVEL
                        Logging level int value (DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50). (default: 20 )
  --hide-url            Hide URL and QR code from the splash screen.
  --prefer-hostname     Use the local hostname instead of the IP as the connection URL. Use at your discretion: mDNS is not guaranteed
                        to work on all LAN configurations. Defaults to False
  --hide-raspiwifi-instructions
                        Hide RaspiWiFi setup instructions from the splash screen.
  --hide-splash-screen, --headless
                        Headless mode. Don't launch the splash screen/player on the pikaraoke server
  --high-quality        Download higher quality video. Note: requires ffmpeg and may cause CPU, download speed, and other performance
                        issues
  --logo-path LOGO_PATH [LOGO_PATH ...]
                        Path to a custom logo image file for the splash screen. Recommended dimensions ~ 2048x1024px
  -u URL, --url URL     Override the displayed IP address with a supplied URL. This argument should include port, if necessary
  -m FFMPEG_URL, --ffmpeg-url FFMPEG_URL
                        Override the ffmpeg address with a supplied URL.
  --hide-overlay        Hide overlay that shows on top of video with pikaraoke QR code and IP
  --admin-password ADMIN_PASSWORD
                        Administrator password, for locking down certain features of the web UI such as queue editing, player controls,
                        song editing, and system shutdown. If unspecified, everyone is an admin.
```

### I'm not hearing audio out of the headphone jack (rpi)

You should be able to right-click the speaker icon in the upper right of the desktop of the OS to change the audio output device. If that fails, see the official raspberry pi docs on [changing audio output](https://www.raspberrypi.com/documentation/computers/configuration.html#change-audio-output)

### How to auto-start PiKaraoke (rpi)

This is optional, but you may want to make your raspberry pi a dedicated karaoke device.

```
mkdir /home/pi/.config/autostart
nano /home/pi/.config/autostart/pikaraoke.desktop
```

If you installed pikaraoke globally with pip, this should work:

```
[Desktop Entry]
Type=Application
Name=Pikaraoke
Exec=pikaraoke
```

If you installed to a .venv, then you may need to adjust the exec path to the full path of the executable

```
[Desktop Entry]
Type=Application
Name=Pikaraoke
Exec=/home/pi/.venv/bin/pikaraoke
```

Restart and it should auto-launch on your next boot.

If you want to kill the pikaraoke process, you can do so from the PiKaraoke Web UI under: `Info > Quit pikaraoke`. Or you can ssh in and run `sudo killall python` or something similar.

Note that if your wifi/network is inactive pikaraoke will error out 10 seconds after being launched. This is to prevent the app from hijacking your ability to login to repair the connection.

### How to keep the screen from turning off when idle (rpi)

Disable "screen blanking" in `raspi-config`. See this [article](https://www.raspberrypi.com/documentation/computers/configuration.html#display-options)

### Songs aren't downloading!

Make sure youtube-dl is up to date, old versions have higher failure rates due to security changes in Youtube. You can see your current version installed by navigating to `Info > System Info > Youtube-dl version`. The version number is usually the date it was released. If this is older than a couple of months, chances are it will need an update.

You can update youtube-dl directly from the web UI. Go to `Info > Update Youtube-dl`

You can also just restart pikaraoke, it checks for updates on every launch.

### Downloads are slow!

youtube-dl is very CPU intensive, especially for single-core devices like the pi models zero and less-than 2. The more simultaneous downloads there are, the longer they will take. Try to limit it to 1-2 at a time. Pi 3 can handle quite a bit more.

### I brought my pikaraoke to a friend's house and it can't connect to their network. How do I change wifi connection without ssh? (rpi)

These are my preferred ways to do it, but they might require either a USB keyboard or a computer with an SD Card reader.

- _USB Keyboard_: plug in a USB keyboard to the pi. After it boots up, log in and run "sudo raspi-config" and configure wifi through the Network Options section. If the desktop UI is installed, you can configure wifi using the GUI wizard (right-click the wifi icon in the top right). You can also manually edit /etc/wpa_supplicant/wpa_supplicant.conf as desribed below.
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

If you run your pi as a wifi access point, your browser can connect to that access point, and it should work. See this [article](https://www.raspberrypi.org/documentation/configuration/wireless/access-point.md).

You can also try [RaspiWiFi](https://github.com/jasbur/RaspiWiFi) (used for configuring wifi connections headless). While it's in AP mode, you can connect to the pi as an AP and connect directly to it at http://10.0.0.1:5555

### Where do I plug in a microphone?

The pi doesn't have a hardware audio input. Technically, you should be able to run a microphone through it with a USB sound card attached to the pi (or USB microphone), but the latency is generally not usable.

Ideally, you'd have a mixer and amplifier that you could run the line out of the pi to, as well as the microphones. I used this affordable wireless microphone set from amazon: https://amzn.to/2OXKXdc (affiliate link) It has a line-in so you can also run PiKaraoke into the mix, and output to an amplifier.

### How do I change song pitch/key?

While a song is playing, the home screen of the web interface will show a transpose slider. Slide it up or down based on your preference and press the "ok" button to restart the song in the given key.

### Some downloads have higher/lower volume than the rest. How can I normalize the audio?

You can try the normalize command line option `pikaraoke --normalize-audio`.

Note that this is rather CPU intensive and might struggle on slower pi devices.

### How do I add cdg or mp3+cdg zip files?

You'll need to add them manually by copying them to the root of your download folder. Run `pikaraoke --help` and look under DOWNLOAD_PATH to find out what the default folder is, or specify your own. Only cdg/mp3 pairs and .zip files are supported.

### My mp3/cdg file is not playing

CDG files must have an mp3 file with a exact matching file name. They can also be bundled together in a single zip file, but the filenames in the zip must still match. They must also be placed in the root of the download directory and not stashed away in sub-directories.

### I'm getting this ChromeDriver error on launch: "session not created: DevToolsActivePort file doesn't exist"

Are you trying to launch over SSH? That probably indicates that chromedriver doesn't know which display to launch the browser on. If so, you may need to specify the native display of the remote device using this command: `DISPLAY=:0.0 pikaraoke`.

You can alternately run headless if you launch the splash screen manually on a separate machine: `pikaraoke --headless`

### How do I dismiss the Splash confirmation screen on an in-TV browser? (like a Samsung TV with web browsing)

The splash confirmation screen is an unfortunate necessity due to modern browser permissions disabling video autoplay. A single interaction will enable it, and the confirmation screen serves as this interaction. Hopefully your TV has a way to click the button on the screen with the remote or otherwise.

If you want to try without confirmation, you can add a parameter to the end of the splash screen URL "confirm=false". Ex: `http://pikaraoke.local:5555/splash?confirm=false` but there's no guarantee that videos will play; it depends on the embedded browser implementation.
