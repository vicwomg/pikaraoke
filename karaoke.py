import glob
import json
import logging
import os
import random
import socket
import subprocess
import sys
import threading
import time
import re
from io import BytesIO
from subprocess import check_output

import pygame
import qrcode
from unidecode import unidecode

from lib import omxclient, vlcclient
from lib.get_platform import get_platform

if get_platform() != "windows":
    from signal import SIGALRM, alarm, signal


class Karaoke:

    raspi_wifi_config_ip = "10.0.0.1"
    raspi_wifi_conf_file = "/etc/raspiwifi/raspiwifi.conf"
    raspi_wifi_config_installed = os.path.exists(raspi_wifi_conf_file)

    queue = []
    available_songs = []
    downloading_songs = {}
    now_playing = None
    now_playing_filename = None
    now_playing_user = None
    now_playing_transpose = 0
    is_paused = True
    process = None
    qr_code_path = None
    base_path = os.path.dirname(__file__)
    volume_offset = 0
    loop_interval = 500  # in milliseconds
    default_logo_path = os.path.join(base_path, "logo.png")

    def __init__(
        self,
        port=5000,
        download_path="/usr/lib/pikaraoke/songs",
        hide_ip=False,
        hide_raspiwifi_instructions=False,
        hide_splash_screen=False,
        omxplayer_adev="both",
        dual_screen=False,
        high_quality=False,
        volume=0,
        log_level=logging.DEBUG,
        splash_delay=2,
        youtubedl_path="/usr/local/bin/youtube-dl",
        omxplayer_path=None,
        use_omxplayer=False,
        use_vlc=True,
        vlc_path=None,
        vlc_port=None,
        logo_path=None,
        show_overlay=False
    ):

        # override with supplied constructor args if provided
        self.port = port
        self.hide_ip = hide_ip
        self.hide_raspiwifi_instructions = hide_raspiwifi_instructions
        self.hide_splash_screen = hide_splash_screen
        self.omxplayer_adev = omxplayer_adev
        self.download_path = download_path
        self.dual_screen = dual_screen
        self.high_quality = high_quality
        self.splash_delay = int(splash_delay)
        self.volume_offset = volume
        self.youtubedl_path = youtubedl_path
        self.omxplayer_path = omxplayer_path
        self.use_omxplayer = use_omxplayer
        self.use_vlc = use_vlc
        self.vlc_path = vlc_path
        self.vlc_port = vlc_port
        self.logo_path = self.default_logo_path if logo_path == None else logo_path
        self.show_overlay = show_overlay

        # other initializations
        self.platform = get_platform()
        self.vlcclient = None
        self.omxclient = None
        self.screen = None

        logging.basicConfig(
            format="[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=int(log_level),
        )

        logging.debug(
            """
    http port: %s
    hide IP: %s
    hide RaspiWiFi instructions: %s,
    hide splash: %s
    splash_delay: %s
    omx audio device: %s
    dual screen: %s
    high quality video: %s
    download path: %s
    default volume: %s
    youtube-dl path: %s
    omxplayer path: %s
    logo path: %s
    Use OMXPlayer: %s
    Use VLC: %s
    VLC path: %s
    VLC port: %s
    log_level: %s
    show overlay: %s"""
            % (
                self.port,
                self.hide_ip,
                self.hide_raspiwifi_instructions,
                self.hide_splash_screen,
                self.splash_delay,
                self.omxplayer_adev,
                self.dual_screen,
                self.high_quality,
                self.download_path,
                self.volume_offset,
                self.youtubedl_path,
                self.omxplayer_path,
                self.logo_path,
                self.use_omxplayer,
                self.use_vlc,
                self.vlc_path,
                self.vlc_port,
                log_level,
                self.show_overlay
            )
        )

        # Generate connection URL and QR code, retry in case pi is still starting up
        # and doesn't have an IP yet (occurs when launched from /etc/rc.local)
        end_time = int(time.time()) + 30

        if self.platform == "raspberry_pi":
            while int(time.time()) < end_time:
                addresses_str = check_output(["hostname", "-I"]).strip().decode("utf-8")
                addresses = addresses_str.split(" ")
                self.ip = addresses[0]
                if not self.is_network_connected():
                    logging.debug("Couldn't get IP, retrying....")
                else:
                    break
        else:
            self.ip = self.get_ip()

        logging.debug("IP address (for QR code and splash screen): " + self.ip)

        self.url = "http://%s:%s" % (self.ip, self.port)

        # get songs from download_path
        self.get_available_songs()

        self.get_youtubedl_version()

        # clean up old sessions
        self.kill_player()

        self.generate_qr_code()
        if self.use_vlc:
            if (self.show_overlay):
                self.vlcclient = vlcclient.VLCClient(port=self.vlc_port, path=self.vlc_path, qrcode=self.qr_code_path, url=self.url)
            else: 
                self.vlcclient = vlcclient.VLCClient(port=self.vlc_port, path=self.vlc_path)
        else:
            self.omxclient = omxclient.OMXClient(path=self.omxplayer_path, adev=self.omxplayer_adev, dual_screen=self.dual_screen, volume_offset=self.volume_offset)

        if not self.hide_splash_screen:
            self.initialize_screen()
            self.render_splash_screen()

 
    # Other ip-getting methods are unreliable and sometimes return 127.0.0.1
    # https://stackoverflow.com/a/28950776
    def get_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(("10.255.255.255", 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = "127.0.0.1"
        finally:
            s.close()
        return IP

    def get_raspi_wifi_conf_vals(self):
        """Extract values from the RaspiWiFi configuration file."""
        f = open(self.raspi_wifi_conf_file, "r")
        
        # Define default values.
        #
        # References: 
        # - https://github.com/jasbur/RaspiWiFi/blob/master/initial_setup.py (see defaults in input prompts)
        # - https://github.com/jasbur/RaspiWiFi/blob/master/libs/reset_device/static_files/raspiwifi.conf
        #
        server_port = "80"
        ssid_prefix = "RaspiWiFi Setup"
        ssl_enabled = "0"
        
        # Override the default values according to the configuration file.
        for line in f.readlines():
            if "server_port=" in line:
                server_port = line.split("t=")[1].strip()
            elif "ssid_prefix=" in line:
                ssid_prefix = line.split("x=")[1].strip()
            elif "ssl_enabled=" in line:
                ssl_enabled = line.split("d=")[1].strip()

        return (server_port, ssid_prefix, ssl_enabled)

    def get_youtubedl_version(self):
        self.youtubedl_version = (
            check_output([self.youtubedl_path, "--version"]).strip().decode("utf8")
        )
        return self.youtubedl_version

    def upgrade_youtubedl(self):
        logging.info(
            "Upgrading youtube-dl, current version: %s" % self.youtubedl_version
        )
        output = check_output([self.youtubedl_path, "-U"]).decode("utf8").strip()
        logging.info(output)
        if "It looks like you installed youtube-dl with a package manager" in output:
            try:
                logging.info("Attempting youtube-dl upgrade via pip3...")
                output = check_output(
                    ["pip3", "install", "--upgrade", "youtube-dl"]
                ).decode("utf8")
            except FileNotFoundError:
                logging.info("Attempting youtube-dl upgrade via pip...")
                output = check_output(
                    ["pip", "install", "--upgrade", "youtube-dl"]
                ).decode("utf8")
            logging.info(output)
        self.get_youtubedl_version()
        logging.info("Done. New version: %s" % self.youtubedl_version)

    def is_network_connected(self):
        return not len(self.ip) < 7

    def generate_qr_code(self):
        logging.debug("Generating URL QR code")
        qr = qrcode.QRCode(
            version=1,
            box_size=1,
            border=4,
        )
        qr.add_data(self.url)
        qr.make()
        img = qr.make_image()
        self.qr_code_path = os.path.join(self.base_path, "qrcode.png")
        img.save(self.qr_code_path)

    def get_default_display_mode(self):
        if self.use_vlc:
            if self.platform == "raspberry_pi":
                os.environ[
                    "SDL_VIDEO_CENTERED"
                ] = "1"  # HACK apparently if display mode is fullscreen the vlc window will be at the bottom of pygame
                return pygame.NOFRAME
            else:
                return pygame.FULLSCREEN
        else:
            return pygame.FULLSCREEN

    def initialize_screen(self):
        if not self.hide_splash_screen:
            logging.debug("Initializing pygame")
            self.full_screen = True
            pygame.display.init()
            pygame.display.set_caption("pikaraoke")
            pygame.font.init()
            pygame.mouse.set_visible(0)
            self.font = pygame.font.SysFont(pygame.font.get_default_font(), 40)
            self.width = pygame.display.Info().current_w
            self.height = pygame.display.Info().current_h
            logging.debug("Initializing screen mode")

            if self.platform == "windows":
                self.screen = pygame.display.set_mode(
                    [self.width, self.height], self.get_default_display_mode()
                )
            else:
                # this section is an unbelievable nasty hack - for some reason Pygame
                # needs a keyboardinterrupt to initialise in some limited circumstances
                # source: https://stackoverflow.com/questions/17035699/pygame-requires-keyboard-interrupt-to-init-display
                class Alarm(Exception):
                    pass

                def alarm_handler(signum, frame):
                    raise Alarm

                signal(SIGALRM, alarm_handler)
                alarm(3)
                try:
                    self.screen = pygame.display.set_mode(
                        [self.width, self.height], self.get_default_display_mode()
                    )
                    alarm(0)
                except Alarm:
                    raise KeyboardInterrupt
            logging.debug("Done initializing splash screen")

    def toggle_full_screen(self):
        if not self.hide_splash_screen:
            logging.debug("Toggling fullscreen...")
            if self.full_screen:
                self.screen = pygame.display.set_mode([1280, 720])
                self.render_splash_screen()
                self.full_screen = False
            else:
                self.screen = pygame.display.set_mode(
                    [self.width, self.height], self.get_default_display_mode()
                )
                self.render_splash_screen()
                self.full_screen = True

    def render_splash_screen(self):
        if not self.hide_splash_screen:
            logging.debug("Rendering splash screen")

            self.screen.fill((0, 0, 0))

            logo = pygame.image.load(self.logo_path)
            logo_rect = logo.get_rect(center=self.screen.get_rect().center)
            self.screen.blit(logo, logo_rect)

            blitY = self.screen.get_rect().bottomleft[1] - 40

            if not self.hide_ip:
                p_image = pygame.image.load(self.qr_code_path)
                p_image = pygame.transform.scale(p_image, (150, 150))
                self.screen.blit(p_image, (20, blitY - 125))
                if not self.is_network_connected():
                    text = self.font.render(
                        "Wifi/Network not connected. Shutting down in 10s...",
                        True,
                        (255, 255, 255),
                    )
                    self.screen.blit(text, (p_image.get_width() + 35, blitY))
                    time.sleep(10)
                    logging.info(
                        "No IP found. Network/Wifi configuration required. For wifi config, try: sudo raspi-config or the desktop GUI: startx"
                    )
                    self.stop()
                else:
                    text = self.font.render(
                        "Connect at: " + self.url, True, (255, 255, 255)
                    )
                    self.screen.blit(text, (p_image.get_width() + 35, blitY))

            if not self.hide_raspiwifi_instructions and (
                self.raspi_wifi_config_installed
                and self.raspi_wifi_config_ip in self.url
            ):
                (server_port, ssid_prefix, ssl_enabled) = self.get_raspi_wifi_conf_vals()

                text1 = self.font.render(
                    "RaspiWifiConfig setup mode detected!", True, (255, 255, 255)
                )
                text2 = self.font.render(
                    "Connect another device/smartphone to the Wifi AP: '%s'" % ssid_prefix,
                    True,
                    (255, 255, 255),
                )
                text3 = self.font.render(
                    "Then point its browser to: '%s://%s%s' and follow the instructions."
                    % ("https" if ssl_enabled == "1" else "http", 
                       self.raspi_wifi_config_ip, 
                       ":%s" % server_port if server_port != "80" else ""),
                    True,
                    (255, 255, 255),
                )
                self.screen.blit(text1, (10, 10))
                self.screen.blit(text2, (10, 50))
                self.screen.blit(text3, (10, 90))

    def render_next_song_to_splash_screen(self):
        if not self.hide_splash_screen:
            self.render_splash_screen()
            if len(self.queue) >= 1:
                logging.debug("Rendering next song to splash screen")
                next_song = self.queue[0]["title"]
                max_length = 60
                if (len(next_song) > max_length):
                    next_song = next_song[0:max_length] + "..."
                next_user = self.queue[0]["user"]
                font_next_song = pygame.font.SysFont(pygame.font.get_default_font(), 60)
                text = font_next_song.render(
                    "Up next: %s" % (unidecode(next_song)), True, (0, 128, 0)
                )
                up_next = font_next_song.render("Up next:  " , True, (255, 255, 0))
                font_user_name = pygame.font.SysFont(pygame.font.get_default_font(), 50)
                user_name = font_user_name.render("Added by: %s " % next_user, True, (255, 120, 0))
                x = self.width - text.get_width() - 10
                y = 5
                self.screen.blit(text, (x, y))
                self.screen.blit(up_next, (x, y))
                self.screen.blit(user_name, (self.width - user_name.get_width() - 10, y + 50))
                return True
            else:
                logging.debug("Could not render next song to splash. No song in queue")
                return False

    def get_search_results(self, textToSearch):
        logging.info("Searching YouTube for: " + textToSearch)
        num_results = 10
        yt_search = 'ytsearch%d:"%s"' % (num_results, unidecode(textToSearch))
        cmd = [self.youtubedl_path, "-j", "--no-playlist", "--flat-playlist", yt_search]
        logging.debug("Youtube-dl search command: " + " ".join(cmd))
        try:
            output = subprocess.check_output(cmd).decode("utf-8")
            logging.debug("Search results: " + output)
            rc = []
            video_url_base = "https://www.youtube.com/watch?v="
            for each in output.split("\n"):
                if len(each) > 2:
                    j = json.loads(each)
                    if (not "title" in j) or (not "url" in j):
                        continue
                    rc.append([j["title"], video_url_base + j["url"], j["id"]])
            return rc
        except Exception as e:
            logging.debug("Error while executing search: " + str(e))
            raise e

    def get_karaoke_search_results(self, songTitle):
        return self.get_search_results(songTitle + " karaoke")

    def download_video(self, song_title, video_url, enqueue=False, user="Pikaraoke"):
        logging.info("Downloading video: " + video_url)
        
        rc = self.call_youtube_dl(song_title, video_url)
        if rc != 0:
            logging.error("Error code while downloading, retrying once...")
            rc = self.call_youtube_dl(song_title,video_url)  # retry once. Seems like this can be flaky
        if rc == 0:
            logging.debug("Song successfully downloaded: " + video_url)
            self.get_available_songs()
            if enqueue:
                y = self.get_youtube_id_from_url(video_url)
                s = self.find_song_by_youtube_id(y)
                if s:
                    self.enqueue(s, user)
                else:
                    logging.error("Error queueing song: " + video_url)
        else:
            logging.error("Error downloading song: " + video_url)
        return rc

    def call_youtube_dl(self, song_title, video_url):
        dl_path = self.download_path + "%(title)s---%(id)s.%(ext)s"
        file_quality = (
            "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
            if self.high_quality
            else "mp4"
        )

        cmd = [self.youtubedl_path, "-f", file_quality, "-o", dl_path, video_url]
        logging.debug("Youtube-dl command: " + " ".join(cmd))

        self.downloading_songs[song_title] = 0

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, universal_newlines=True)
        for line in iter(proc.stdout.readline, b''):
            if line != None and line != '' and line != '\n':
                print(line)
                r = re.compile('\[download\]\s*([\d\.]+).*')
                m = r.match(line.rstrip())
                if m:
                    try:
                        progress = m.group(1)
                        progressFloat = float(progress)
                        if progressFloat == 100:
                            self.downloading_songs.pop(song_title, None)
                        else:
                            self.downloading_songs[song_title] = progressFloat
                    except ValueError:
                        logging.warn("Can't parse download progress as float")
                        self.downloading_songs.pop(song_title, None)
        
        self.downloading_songs.pop(song_title, None)
        return proc.returncode

    def get_download_progress(self):
        return self.downloading_songs

    def get_available_songs(self):
        logging.debug("Fetching available songs in: " + self.download_path)
        types = ['*.mp4', '*.mp3', '*.zip', '*.mkv', '*.avi', '*.webm', '*.mov'] 
        if self.platform != "windows":
            # Only non-windows. If we include extra casings, windows shows dups
            types_caps = []
            for ext in types:
                types_caps.append(ext.upper())
                types_caps.append(ext.title())
            types = types + types_caps
        files_grabbed = []
        for files in types:
            files_grabbed.extend(glob.glob(u"%s/**/%s" % (self.download_path, files), recursive=True))
        self.available_songs = sorted(files_grabbed, key=lambda f: str.lower(os.path.basename(f)))

    def delete(self, song_path):
        logging.info("Deleting song: " + song_path)
        os.remove(song_path)
        ext = os.path.splitext(song_path)
        # if we have an associated cdg file, delete that too
        cdg_file = song_path.replace(ext[1],".cdg")
        if (os.path.exists(cdg_file)):
            os.remove(cdg_file)
        
        self.get_available_songs()

    def rename(self, song_path, new_name):
        logging.info("Renaming song: '" + song_path + "' to: " + new_name)
        ext = os.path.splitext(song_path)
        if len(ext) == 2:
            new_file_name = new_name + ext[1]
        os.rename(song_path, self.download_path + new_file_name)
        # if we have an associated cdg file, rename that too
        cdg_file = song_path.replace(ext[1],".cdg")
        if (os.path.exists(cdg_file)):
            os.rename(cdg_file, self.download_path + new_name + ".cdg")
        self.get_available_songs()

    def filename_from_path(self, file_path):
        rc = os.path.basename(file_path)
        rc = os.path.splitext(rc)[0]
        rc = rc.split("---")[0]  # removes youtube id if present
        return rc

    def find_song_by_youtube_id(self, youtube_id):
        for each in self.available_songs:
            if youtube_id in each:
                return each
        logging.error("No available song found with youtube id: " + youtube_id)
        return None

    def get_youtube_id_from_url(self, url):
        s = url.split("watch?v=")
        if len(s) == 2:
            return s[1]
        else:
            logging.error("Error parsing youtube id from url: " + url)
            return None

    def kill_player(self):
        if self.use_vlc:
            logging.debug("Killing old VLC processes")
            if self.vlcclient != None:
                self.vlcclient.kill()
        else:
            if self.omxclient != None:
                self.omxclient.kill()

    def play_file(self, file_path, semitones=0):
        self.now_playing = self.filename_from_path(file_path)
        self.now_playing_filename = file_path

        if self.use_vlc:
            logging.info("Playing video in VLC: " + self.now_playing)
            if semitones == 0:
                self.vlcclient.play_file(file_path)
            else:
                self.vlcclient.play_file_transpose(file_path, semitones)
        else:
            logging.info("Playing video in omxplayer: " + self.now_playing)
            self.omxclient.play_file(file_path)

        self.is_paused = False
        self.render_splash_screen()  # remove old previous track

    def transpose_current(self, semitones):
        if self.use_vlc:
            logging.info("Transposing song by %s semitones" % semitones)
            self.now_playing_transpose = semitones
            self.play_file(self.now_playing_filename, semitones)
        else:
            logging.error("Not using VLC. Can't transpose track.")

    def is_file_playing(self):
        if self.use_vlc:
            if self.vlcclient != None and self.vlcclient.is_running():
                return True
            else:
                self.now_playing = None
                return False
        else:
            if self.omxclient != None and self.omxclient.is_running():
                return True
            else:
                self.now_playing = None
                return False

    def is_song_in_queue(self, song_path):
        for each in self.queue:
            if each["file"] == song_path:
                return True
        return False

    def enqueue(self, song_path, user="Pikaraoke"):
        if (self.is_song_in_queue(song_path)):
            logging.warn("Song is already in queue, will not add: " + song_path)   
            return False
        else:
            logging.info("'%s' is adding song to queue: %s" % (user, song_path))
            self.queue.append({"user": user, "file": song_path, "title": self.filename_from_path(song_path)})
            return True

    def queue_add_random(self, amount):
        logging.info("Adding %d random songs to queue" % amount)
        songs = list(self.available_songs)  # make a copy
        if len(songs) == 0:
            logging.warn("No available songs!")
            return False
        i = 0
        while i < amount:
            r = random.randint(0, len(songs) - 1)
            if self.is_song_in_queue(songs[r]):
                logging.warn("Song already in queue, trying another... " + songs[r])
            else:
                self.queue.append({"user": "Randomizer", "file": songs[r], "title": self.filename_from_path(songs[r])})
                i += 1
            songs.pop(r)
            if len(songs) == 0:
                logging.warn("Ran out of songs!")
                return False
        return True

    def queue_clear(self):
        logging.info("Clearing queue!")
        self.queue = []
        self.skip()

    def queue_edit(self, song_name, action):
        index = 0
        song = None
        for each in self.queue:
            if song_name in each["file"]:
                song = each
                break
            else:
                index += 1
        if song == None:
            logging.error("Song not found in queue: " + song["file"])
            return False
        if action == "up":
            if index < 1:
                logging.warn("Song is up next, can't bump up in queue: " + song["file"])
                return False
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                return True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warn(
                    "Song is already last, can't bump down in queue: " + song["file"]
                )
                return False
            else:
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
                return True
        elif action == "delete":
            logging.info("Deleting song from queue: " + song["file"])
            del self.queue[index]
            return True
        else:
            logging.error("Unrecognized direction: " + action)
            return False

    def skip(self):
        if self.is_file_playing():
            logging.info("Skipping: " + self.now_playing)
            if self.use_vlc:
                self.vlcclient.stop()
            else:
                self.omxclient.stop()
            self.reset_now_playing()
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self):
        if self.is_file_playing():
            logging.info("Toggling pause: " + self.now_playing)
            if self.use_vlc:
                if self.vlcclient.is_playing():
                    self.vlcclient.pause()
                else:
                    self.vlcclient.play()
            else:
                if self.omxclient.is_playing():
                    self.omxclient.pause()
                else:
                    self.omxclient.play()
            self.is_paused = not self.is_paused
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def vol_up(self):
        if self.is_file_playing():
            if self.use_vlc:
                self.vlcclient.vol_up()
            else:
                self.omxclient.vol_up()
            return True
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            return False

    def vol_down(self):
        if self.is_file_playing():
            if self.use_vlc:
                self.vlcclient.vol_down()
            else:
                self.omxclient.vol_down()
            return True
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            return False

    def restart(self):
        if self.is_file_playing():
            if self.use_vlc:
                self.vlcclient.restart()
            else:
                self.omxclient.restart()
            self.is_paused = False
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self):
        self.running = False

    def handle_run_loop(self):
        if self.hide_splash_screen:
            time.sleep(self.loop_interval / 1000)
        else:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    logging.warn("Window closed: Exiting pikaraoke...")
                    self.running = False
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        logging.warn("ESC pressed: Exiting pikaraoke...")
                        self.running = False
                    if event.key == pygame.K_f:
                        self.toggle_full_screen()
            pygame.display.update()
            pygame.time.wait(self.loop_interval)

    # Use this to reset the screen in case it loses focus
    # This seems to occur in windows after playing a video
    def pygame_reset_screen(self):
        if self.hide_splash_screen:
            pass
        else:
            logging.debug("Resetting pygame screen...")
            pygame.display.quit()
            self.initialize_screen()
            self.render_splash_screen()

    def reset_now_playing(self):
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.is_paused = True
        self.now_playing_transpose = 0

    def run(self):
        logging.info("Starting PiKaraoke!")
        self.running = True
        while self.running:
            try:
                if not self.is_file_playing() and self.now_playing != None:
                    self.reset_now_playing()
                if len(self.queue) > 0:
                    if not self.is_file_playing():
                        self.reset_now_playing()
                        if not pygame.display.get_active():
                            self.pygame_reset_screen()
                        self.render_next_song_to_splash_screen()
                        i = 0
                        while i < (self.splash_delay * 1000):
                            self.handle_run_loop()
                            i += self.loop_interval
                        self.play_file(self.queue[0]["file"])
                        self.now_playing_user=self.queue[0]["user"]
                        self.queue.pop(0)
                elif not pygame.display.get_active() and not self.is_file_playing():
                    self.pygame_reset_screen()
                self.handle_run_loop()
            except KeyboardInterrupt:
                logging.warn("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False
