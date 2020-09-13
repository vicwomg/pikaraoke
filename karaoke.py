import glob
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
from io import BytesIO
from socket import gethostbyname, gethostname
from subprocess import check_output

import pygame
import qrcode
import vlcclient
from get_platform import get_platform
from unidecode import unidecode

if get_platform() != "windows":
    from signal import SIGALRM, alarm, signal


class Karaoke:

    overlay_file_path = "/tmp/pikaraoke-overlay.srt"
    raspi_wifi_config_ip = "10.0.0.1"
    raspi_wifi_conf_file = "/etc/raspiwifi/raspiwifi.conf"
    raspi_wifi_config_installed = os.path.exists(raspi_wifi_conf_file)

    queue = []
    available_songs = []
    now_playing = None
    is_pause = True
    process = None
    qr_code = None
    base_path = os.path.dirname(__file__)
    volume_offset = 0
    loop_interval = 500  # in milliseconds

    def __init__(
        self,
        port=5000,
        download_path="/usr/lib/pikaraoke/songs",
        hide_ip=False,
        hide_splash_screen=False,
        hide_overlay=True,
        alsa_fix=False,
        dual_screen=False,
        high_quality=False,
        volume=0,
        log_level=logging.DEBUG,
        splash_delay=2,
        youtubedl_path="/usr/local/bin/youtube-dl",
        omxplayer_path="/usr/bin/omxplayer",
        use_vlc=False,
        vlc_path=None,
        vlc_port=None,
    ):

        # override with supplied constructor args if provided
        self.port = port
        self.hide_ip = hide_ip
        self.hide_splash_screen = hide_splash_screen
        self.alsa_fix = alsa_fix
        self.download_path = download_path
        self.dual_screen = dual_screen
        self.high_quality = high_quality
        self.splash_delay = int(splash_delay)
        self.hide_overlay = hide_overlay
        self.volume_offset = volume
        self.youtubedl_path = youtubedl_path
        self.player_path = omxplayer_path
        self.use_vlc = use_vlc
        self.vlc_path = vlc_path
        self.vlc_port = vlc_port

        # other initializations
        self.platform = get_platform()
        self.vlcclient = None
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
    hide splash: %s
    splash_delay: %s
    hide overlay: %s
    alsa fix: %s
    dual screen: %s
    high quality video: %s
    download path: %s
    default volume: %s
    youtube-dl path: %s
    omxplayer path: %s
    Use VLC: %s
    VLC path: %s
    VLC port: %s
    log_level: %s"""
            % (
                self.port,
                self.hide_ip,
                self.hide_splash_screen,
                self.splash_delay,
                self.hide_overlay,
                self.alsa_fix,
                self.dual_screen,
                self.high_quality,
                self.download_path,
                self.volume_offset,
                self.youtubedl_path,
                self.player_path,
                self.use_vlc,
                self.vlc_path,
                self.vlc_port,
                log_level,
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
            self.ip = gethostbyname(gethostname())

        self.url = "http://%s:%s" % (self.ip, self.port)

        # get songs from download_path
        self.get_available_songs()

        self.get_youtubedl_version()

        # clean up old sessions
        self.kill_player()
        if os.path.isfile(self.overlay_file_path):
            os.remove(self.overlay_file_path)

        if not self.hide_splash_screen:
            self.generate_qr_code()
            self.initialize_screen()
            self.render_splash_screen()

    def get_raspi_wifi_ap(self):
        f = open(self.raspi_wifi_conf_file, "r")
        for line in f.readlines():
            if "ssid_prefix=" in line:
                return line.split("x=")[1].strip()
        return False

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

    def generate_overlay_file(self, file_path):
        if not self.hide_overlay:
            current_song = self.filename_from_path(file_path)
            logging.debug("Generating overlay file")
            if not self.hide_ip:
                msg = "PiKaraoke IP: %s" % self.url
            else:
                msg = ""
            output = "00:00:00,00 --> 00:00:30,00 \n%s\n%s" % (current_song, msg)
            f = open(self.overlay_file_path, "w")
            try:
                f.write(output.encode("utf-8"))
            except TypeError:
                # python 3 hack
                f.write(output)
            logging.debug("Done generating overlay file: " + output)

    def generate_qr_code(self):
        logging.debug("Generating URL QR code")
        img = qrcode.make(self.url)
        img.save(os.path.join(self.base_path, "qrcode.png"))

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

            logo = pygame.image.load(os.path.join(self.base_path, "logo.png"))
            logo_rect = logo.get_rect(center=self.screen.get_rect().center)
            self.screen.blit(logo, logo_rect)

            if not self.hide_ip:
                p_image = pygame.image.load(os.path.join(self.base_path, "qrcode.png"))
                p_image = pygame.transform.scale(p_image, (150, 150))
                self.screen.blit(p_image, (20, 20))
                if not self.is_network_connected():
                    text = self.font.render(
                        "Wifi/Network not connected. Shutting down in 10s...",
                        True,
                        (255, 255, 255),
                    )
                    self.screen.blit(text, (p_image.get_width() + 35, 20))
                    time.sleep(10)
                    sys.exit(
                        "No IP found. Network/Wifi configuration required. For wifi config, try: sudo raspi-config or the desktop GUI: startx"
                    )
                else:
                    text = self.font.render(
                        "Connect at: " + self.url, True, (255, 255, 255)
                    )
                    self.screen.blit(text, (p_image.get_width() + 35, 20))

            if (
                self.raspi_wifi_config_installed
                and self.raspi_wifi_config_ip in self.url
            ):
                ap = self.get_raspi_wifi_ap()
                text1 = self.font.render(
                    "RaspiWifiConfig setup mode detected!", True, (255, 255, 255)
                )
                text2 = self.font.render(
                    "Connect another device/smartphone to the Wifi AP: '%s'" % ap,
                    True,
                    (255, 255, 255),
                )
                text3 = self.font.render(
                    "Then point its browser to: 'http://%s' and follow the instructions."
                    % self.raspi_wifi_config_ip,
                    True,
                    (255, 255, 255),
                )
                y1 = self.height - text1.get_height() - 80
                y2 = self.height - text2.get_height() - 40
                y3 = self.height - text2.get_height() - 5
                self.screen.blit(text1, (10, y1))
                self.screen.blit(text2, (10, y2))
                self.screen.blit(text3, (10, y3))

    def render_next_song_to_splash_screen(self):
        if not self.hide_splash_screen:
            self.render_splash_screen()
            if len(self.queue) >= 1:
                logging.debug("Rendering next song to splash screen")
                next_song = self.filename_from_path(self.queue[0])
                font_next_song = pygame.font.SysFont(pygame.font.get_default_font(), 60)
                text = font_next_song.render(
                    "Up next:  " + next_song, True, (0, 128, 0)
                )
                up_next = font_next_song.render("Up next:  ", True, (255, 255, 0))
                x = self.width - text.get_width() - 10
                y = self.height - text.get_height() - 5
                self.screen.blit(text, (x, y))
                self.screen.blit(up_next, (x, y))
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

    def download_video(self, video_url, enqueue=False):
        logging.info("Downloading video: " + video_url)
        dl_path = self.download_path + "%(title)s---%(id)s.%(ext)s"
        file_quality = (
            "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
            if self.high_quality
            else "mp4"
        )
        cmd = [self.youtubedl_path, "-f", file_quality, "-o", dl_path, video_url]
        logging.debug("Youtube-dl command: " + " ".join(cmd))
        rc = subprocess.call(cmd)
        if rc != 0:
            logging.error("Error code while downloading, retrying once...")
            rc = subprocess.call(cmd)  # retry once. Seems like this can be flaky
        if rc == 0:
            logging.debug("Song successfully downloaded: " + video_url)
            self.get_available_songs()
            if enqueue:
                y = self.get_youtube_id_from_url(video_url)
                s = self.find_song_by_youtube_id(y)
                if s:
                    self.enqueue(s)
                else:
                    logging.error("Error queueing song: " + video_url)
        else:
            logging.error("Error downloading song: " + video_url)
        return rc

    def get_available_songs(self):
        logging.debug("Fetching available songs in: " + self.download_path)
        self.available_songs = sorted(glob.glob(u"%s/*" % self.download_path))

    def delete(self, song_path):
        logging.info("Deleting song: " + song_path)
        os.remove(song_path)
        self.get_available_songs()

    def rename(self, song_path, new_name):
        logging.info("Renaming song: '" + song_path + "' to: " + new_name)
        ext = os.path.splitext(song_path)
        if len(ext) == 2:
            new_name = new_name + ext[1]
        os.rename(song_path, self.download_path + new_name)

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
            logging.debug("Killing old omxplayer processes")
            player_kill = ["killall", "omxplayer.bin"]
            FNULL = open(os.devnull, "w")
            subprocess.Popen(
                player_kill, stdin=subprocess.PIPE, stdout=FNULL, stderr=FNULL
            )

    def play_file(self, file_path):
        self.now_playing = self.filename_from_path(file_path)
        if (not self.hide_overlay) and (not self.use_vlc):
            self.generate_overlay_file(file_path)

        self.kill_player()

        if self.use_vlc:
            logging.info("Playing video in VLC: " + self.now_playing)
            self.vlcclient = vlcclient.VLCClient(port=self.vlc_port, path=self.vlc_path)
            self.vlcclient.play_file(file_path)
        else:
            logging.info("Playing video in omxplayer: " + self.now_playing)
            output = "alsa:hw:0,0" if self.alsa_fix else "both"
            cmd = [
                self.player_path,
                file_path,
                "--blank",
                "-o",
                output,
                "--vol",
                str(self.volume_offset),
                "--font-size",
                str(25),
            ]
            if self.dual_screen:
                cmd += ["--display", "7"]

            if not self.hide_overlay:
                cmd += ["--subtitles", self.overlay_file_path]
            logging.debug("Player command: " + " ".join(cmd))
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE)

        self.is_pause = False
        self.render_splash_screen()  # remove old previous track

    def is_file_playing(self):
        if self.use_vlc:
            if self.vlcclient != None and self.vlcclient.is_running():
                return True
            else:
                self.now_playing = None
                return False
        else:
            if self.process == None:
                self.now_playing = None
                return False
            elif self.process.poll() == None:
                return True
            else:
                self.now_playing = None
                return False

    def enqueue(self, song_path):
        if song_path in self.queue:
            logging.warn("Song already in queue, will not add: " + song_path)
            return False
        else:
            logging.info("Adding video to queue: " + song_path)
            self.queue.append(song_path)
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
            if songs[r] in self.queue:
                logging.warn("Song already in queue, trying another... " + songs[r])
            else:
                self.queue.append(songs[r])
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
        song_path = None
        for each in self.queue:
            if song_name in each:
                song_path = each
                break
            else:
                index += 1
        if song_path == None:
            logging.error("Song not found in queue: " + song_name)
            return False
        if action == "up":
            if index < 2:
                logging.warn(
                    "Song is now playing or up next, can't bump up in queue: "
                    + song_path
                )
                return False
            else:
                logging.info("Bumping song up in queue: " + song_path)
                del self.queue[index]
                self.queue.insert(index - 1, song_path)
                return True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warn(
                    "Song is already last, can't bump down in queue: " + song_path
                )
                return False
            if index == 0:
                logging.warn(
                    "Song is currently playing, can't bump down in queue: " + song_path
                )
                return False
            else:
                logging.info("Bumping song down in queue: " + song_path)
                del self.queue[index]
                self.queue.insert(index + 1, song_path)
                return True
        elif action == "delete":
            if index == 0:
                self.skip()
                logging.warn("Song is currently playing, skipping: " + song_path)
                return True
            else:
                logging.info("Deleting song from queue: " + song_path)
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
                self.process.stdin.write("q".encode("utf-8"))
                self.process.stdin.flush()
            self.now_playing = None
            self.is_pause = True
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
                self.process.stdin.write("p".encode("utf-8"))
                self.process.stdin.flush()
            self.is_pause = not self.is_pause
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def vol_up(self):
        if self.is_file_playing():
            if self.use_vlc:
                self.vlcclient.vol_up()
                self.volume_offset = self.vlcclient.get_volume()
            else:
                logging.info("Volume up: " + self.now_playing)
                self.process.stdin.write("=".encode("utf-8"))
                self.process.stdin.flush()
                self.volume_offset += 300
            return True
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            return False

    def vol_down(self):
        if self.is_file_playing():
            if self.use_vlc:
                self.vlcclient.vol_down()
                self.volume_offset = self.vlcclient.get_volume()
            else:
                logging.info("Volume down: " + self.now_playing)
                self.process.stdin.write("-".encode("utf-8"))
                self.volume_offset -= 300
            return True
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            return False

    def restart(self):
        if self.is_file_playing():
            if self.use_vlc:
                self.vlcclient.restart()
            else:
                logging.info("Restarting: " + self.now_playing)
                self.process.stdin.write("i".encode("utf-8"))
                self.process.stdin.flush()
                self.is_pause = False
            return True

        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self):
        self.running = False

    def pygame_event_loop(self):
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

    def run(self):
        logging.info("Starting PiKaraoke!")
        self.running = True
        while self.running:
            try:
                if len(self.queue) > 0:
                    if not self.is_file_playing():
                        self.render_next_song_to_splash_screen()
                        i = 0
                        while i < (self.splash_delay * 1000):
                            logging.debug(i)
                            self.pygame_event_loop()
                            i += self.loop_interval
                        self.play_file(self.queue[0])
                        self.queue.pop(0)
                self.pygame_event_loop()
            except KeyboardInterrupt:
                logging.warn("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False
