import configparser
import contextlib
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue
from subprocess import CalledProcessError, check_output
from threading import Thread
from urllib.parse import urlparse

import qrcode
from flask_babel import _
from unidecode import unidecode

from pikaraoke.lib.ffmpeg import (
    build_ffmpeg_cmd,
    get_ffmpeg_version,
    is_transpose_enabled,
)
from pikaraoke.lib.file_resolver import (
    FileResolver,
    delete_tmp_dir,
    is_transcoding_required,
)
from pikaraoke.lib.get_platform import (
    get_os_version,
    get_platform,
    is_raspberry_pi,
    supports_hardware_h264_encoding,
)


# Support function for reading  lines from ffmpeg stderr without blocking
def enqueue_output(out, queue):
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


def decode_ignore(input):
    return input.decode("utf-8", "ignore").strip()


class Karaoke:
    raspi_wifi_config_ip = "10.0.0.1"
    raspi_wifi_conf_file = "/etc/raspiwifi/raspiwifi.conf"
    raspi_wifi_config_installed = os.path.exists(raspi_wifi_conf_file)

    queue = []
    available_songs = []

    # These all get sent to the /nowplaying endpoint for client-side polling
    now_playing = None
    now_playing_filename = None
    now_playing_user = None
    now_playing_transpose = 0
    now_playing_duration = None
    now_playing_url = None
    now_playing_command = None

    is_playing = False
    is_paused = True
    process = None
    qr_code_path = None
    base_path = os.path.dirname(__file__)
    volume = None
    loop_interval = 500  # in milliseconds
    default_logo_path = os.path.join(base_path, "logo.png")
    default_bg_music_path = os.path.join(base_path, "static/music/")
    screensaver_timeout = 300  # in seconds

    ffmpeg_process = None
    ffmpeg_log = None
    ffmpeg_version = get_ffmpeg_version()
    is_transpose_enabled = is_transpose_enabled()
    normalize_audio = False

    raspberry_pi = is_raspberry_pi()
    os_version = get_os_version()

    config_obj = configparser.ConfigParser()

    def __init__(
        self,
        port=5555,
        download_path="/usr/lib/pikaraoke/songs",
        hide_url=False,
        hide_notifications=False,
        hide_raspiwifi_instructions=False,
        hide_splash_screen=False,
        high_quality=False,
        volume=0.85,
        normalize_audio=False,
        complete_transcode_before_play=False,
        buffer_size=150,
        log_level=logging.DEBUG,
        splash_delay=2,
        youtubedl_path="/usr/local/bin/yt-dlp",
        logo_path=None,
        hide_overlay=False,
        screensaver_timeout=300,
        url=None,
        prefer_hostname=True,
        disable_bg_music=False,
        bg_music_volume=0.3,
        bg_music_path=None,
        disable_score=False,
        limit_user_songs_by=0,
    ):
        logging.basicConfig(
            format="[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=int(log_level),
        )

        # override with supplied constructor args if provided
        self.port = port
        self.hide_url = self.get_user_preference("hide_url") or hide_url
        self.hide_notifications = (
            self.get_user_preference("hide_notifications") or hide_notifications
        )
        self.hide_raspiwifi_instructions = hide_raspiwifi_instructions
        self.hide_splash_screen = hide_splash_screen
        self.download_path = download_path
        self.high_quality = self.get_user_preference("high_quality") or high_quality
        self.splash_delay = self.get_user_preference("splash_delay") or int(splash_delay)
        self.volume = self.get_user_preference("volume") or volume
        self.normalize_audio = self.get_user_preference("normalize_audio") or normalize_audio
        self.complete_transcode_before_play = (
            self.get_user_preference("complete_transcode_before_play")
            or complete_transcode_before_play
        )
        self.buffer_size = self.get_user_preference("buffer_size") or buffer_size
        self.youtubedl_path = youtubedl_path
        self.logo_path = self.default_logo_path if logo_path == None else logo_path
        self.hide_overlay = self.get_user_preference("hide_overlay") or hide_overlay
        self.screensaver_timeout = (
            self.get_user_preference("screensaver_timeout") or screensaver_timeout
        )
        self.url_override = url
        self.prefer_hostname = prefer_hostname
        self.disable_bg_music = self.get_user_preference("disable_bg_music") or disable_bg_music
        self.bg_music_volume = self.get_user_preference("bg_music_volume") or bg_music_volume
        self.bg_music_path = self.default_bg_music_path if bg_music_path == None else bg_music_path
        self.disable_score = self.get_user_preference("disable_score") or disable_score
        self.limit_user_songs_by = (
            self.get_user_preference("limit_user_songs_by") or limit_user_songs_by
        )

        # other initializations
        self.platform = get_platform()
        self.screen = None

        logging.debug(
            f"""
    http port: {self.port}
    hide URL: {self.hide_url}
    prefer hostname: {self.prefer_hostname}
    url override: {self.url_override}
    hide RaspiWiFi instructions: {self.hide_raspiwifi_instructions}
    headless (hide splash): {self.hide_splash_screen}
    splash_delay: {self.splash_delay}
    screensaver_timeout: {self.screensaver_timeout}
    high quality video: {self.high_quality}
    download path: {self.download_path}
    default volume: {self.volume}
    normalize audio: {self.normalize_audio}
    complete transcode before play: {self.complete_transcode_before_play}
    buffer size (kb): {self.buffer_size}
    youtube-dl path: {self.youtubedl_path}
    logo path: {self.logo_path}
    log_level: {log_level}
    hide overlay: {self.hide_overlay}
    disable bg music: {self.disable_bg_music}
    bg music volume: {self.bg_music_volume}
    bg music path: {self.bg_music_path}
    disable score: {self.disable_score}
    limit user songs by: {self.limit_user_songs_by}
    hide notifications: {self.hide_notifications}

    platform: {self.platform}
    os version: {self.os_version}
    ffmpeg version: {self.ffmpeg_version}
    ffmpeg transpose support: {self.is_transpose_enabled}
    hardware h264 encoding: {supports_hardware_h264_encoding()}
    youtubedl-version: {self.get_youtubedl_version()}
"""
        )

        # Generate connection URL and QR code,
        if self.raspberry_pi:
            # retry in case pi is still starting up
            # and doesn't have an IP yet (occurs when launched from /etc/rc.local)
            end_time = int(time.time()) + 30
            while int(time.time()) < end_time:
                addresses_str = check_output(["hostname", "-I"]).strip().decode("utf-8", "ignore")
                addresses = addresses_str.split(" ")
                self.ip = addresses[0]
                if not self.is_network_connected():
                    logging.debug("Couldn't get IP, retrying....")
                else:
                    break
        else:
            self.ip = self.get_ip()

        logging.debug("IP address (for QR code and splash screen): " + self.ip)

        if self.url_override != None:
            logging.debug("Overriding URL with " + self.url_override)
            self.url = self.url_override
        else:
            if self.prefer_hostname:
                self.url = f"http://{socket.getfqdn().lower()}:{self.port}"
            else:
                self.url = f"http://{self.ip}:{self.port}"
        self.url_parsed = urlparse(self.url)

        # get songs from download_path
        self.get_available_songs()

        self.get_youtubedl_version()

        self.generate_qr_code()

    # def get_user_preferences(self, preference):
    def get_user_preference(self, preference, default_value=False):
        # Try to read the config file
        try:
            self.config_obj.read("config.ini")
        except FileNotFoundError:
            return default_value

        # Check if the section exists
        if not self.config_obj.has_section("USERPREFERENCES"):
            return default_value

        # Try to get the value
        try:
            pref = self.config_obj.get("USERPREFERENCES", preference)
            if pref == "True":
                return True
            elif pref == "False":
                return False
            elif pref.isnumeric():
                return int(pref)
            elif pref.replace(".", "", 1).isdigit():
                return float(pref)
            else:
                return pref

        except (configparser.NoOptionError, ValueError):
            return default_value

    def change_preferences(self, preference, val):
        """Makes changes in the config.ini file that stores the user preferences.
        Receives the preference and it's new value"""

        logging.debug("Changing user preference << %s >> to %s" % (preference, val))
        try:
            if "USERPREFERENCES" not in self.config_obj:
                self.config_obj.add_section("USERPREFERENCES")

            userprefs = self.config_obj["USERPREFERENCES"]
            userprefs[preference] = str(val)
            setattr(self, preference, eval(str(val)))
            with open("config.ini", "w") as conf:
                self.config_obj.write(conf)
                self.changed_preferences = True
            return [True, _("Your preferences were changed successfully")]
        except Exception as e:
            logging.debug("Failed to change user preference << %s >>: %s", preference, e)
            return [False, _("Something went wrong! Your preferences were not changed")]

    def clear_preferences(self):
        try:
            os.remove("config.ini")
            return [True, _("Your preferences were cleared successfully")]
        except OSError:
            return [False, _("Something went wrong! Your preferences were not cleared")]

    def get_ip(self):
        # python socket.connect will not work on android, access denied. Workaround: use ifconfig which is installed to termux by default, iirc.
        if self.platform == "android":
            # shell command is: ifconfig 2> /dev/null | awk '/wlan0/{flag=1} flag && /inet /{print $2; exit}'
            IP = (
                subprocess.check_output(
                    "ifconfig 2> /dev/null | awk '/wlan0/{flag=1} flag && /inet /{print $2; exit}'",
                    shell=True,
                )
                .decode("utf8")
                .strip()
            )
        else:
            # Other ip-getting methods are unreliable and sometimes return 125.0.0.1
            # https://stackoverflow.com/a/28950774
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
        logging.info("Upgrading youtube-dl, current version: %s" % self.youtubedl_version)
        try:
            output = (
                check_output([self.youtubedl_path, "-U"], stderr=subprocess.STDOUT)
                .decode("utf8")
                .strip()
            )
        except CalledProcessError as e:
            output = e.output.decode("utf8")
        logging.info(output)
        if "You installed yt-dlp with pip or using the wheel from PyPi" in output:
            # allow pip to break system packages (probably required if installed without venv)
            args = ["install", "--upgrade", "yt-dlp", "--break-system-packages"]
            try:
                logging.info("Attempting youtube-dl upgrade via pip3...")
                output = (
                    check_output(["pip3"] + args, stderr=subprocess.STDOUT).decode("utf8").strip()
                )
            except FileNotFoundError:
                logging.info("Attempting youtube-dl upgrade via pip...")
                output = (
                    check_output(["pip"] + args, stderr=subprocess.STDOUT).decode("utf8").strip()
                )
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

    def get_search_results(self, textToSearch):
        logging.info("Searching YouTube for: " + textToSearch)
        num_results = 10
        yt_search = 'ytsearch%d:"%s"' % (num_results, unidecode(textToSearch))
        cmd = [self.youtubedl_path, "-j", "--no-playlist", "--flat-playlist", yt_search]
        logging.debug("Youtube-dl search command: " + " ".join(cmd))
        try:
            output = subprocess.check_output(cmd).decode("utf-8", "ignore")
            logging.debug("Search results: " + output)
            rc = []
            for each in output.split("\n"):
                if len(each) > 2:
                    j = json.loads(each)
                    if (not "title" in j) or (not "url" in j):
                        continue
                    rc.append([j["title"], j["url"], j["id"]])
            return rc
        except Exception as e:
            logging.debug("Error while executing search: " + str(e))
            raise e

    def get_karaoke_search_results(self, songTitle):
        return self.get_search_results(songTitle + " karaoke")

    def send_message_to_splash(self, message, color="primary"):
        # Color should be bulma compatible: primary, warning, success, danger
        if not self.hide_notifications:
            self.send_command("message::" + message + "::is-" + color)

    def log_and_send(self, message, category="info"):
        # Category should be one of: info, success, warning, danger
        if category == "success":
            logging.info(message)
            self.send_message_to_splash(message, "success")
        elif category == "warning":
            logging.warning(message)
            self.send_message_to_splash(message, "warning")
        elif category == "danger":
            logging.error(message)
            self.send_message_to_splash(message, "danger")
        else:
            logging.info(message)
            self.send_message_to_splash(message, "primary")

    def download_video(self, video_url, enqueue=False, user="Pikaraoke", title=None):
        displayed_title = title if title else video_url
        # MSG: Message shown after the download is started
        self.log_and_send(_("Downloading video: %s" % displayed_title))
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
            if enqueue:
                # MSG: Message shown after the download is completed and queued
                self.log_and_send(_("Downloaded and queued: %s" % displayed_title), "success")
            else:
                # MSG: Message shown after the download is completed but not queued
                self.log_and_send(_("Downloaded: %s" % displayed_title), "success")
            self.get_available_songs()
            if enqueue:
                y = self.get_youtube_id_from_url(video_url)
                s = self.find_song_by_youtube_id(y)
                if s:
                    self.enqueue(s, user, log_action=False)
                else:
                    # MSG: Message shown after the download is completed but the adding to queue fails
                    self.log_and_send(_("Error queueing song: ") + displayed_title, "danger")
        else:
            # MSG: Message shown after the download process is completed but the song is not found
            self.log_and_send(_("Error downloading song: ") + displayed_title, "danger")
        return rc

    def get_available_songs(self):
        logging.info("Fetching available songs in: " + self.download_path)
        types = [".mp4", ".mp3", ".zip", ".mkv", ".avi", ".webm", ".mov"]
        files_grabbed = []
        P = Path(self.download_path)
        for file in P.rglob("*.*"):
            base, ext = os.path.splitext(file.as_posix())
            if ext.lower() in types:
                if os.path.isfile(file.as_posix()):
                    logging.debug("adding song: " + file.name)
                    files_grabbed.append(file.as_posix())

        self.available_songs = sorted(files_grabbed, key=lambda f: str.lower(os.path.basename(f)))

    def delete(self, song_path):
        logging.info("Deleting song: " + song_path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(song_path)
        ext = os.path.splitext(song_path)
        # if we have an associated cdg file, delete that too
        cdg_file = song_path.replace(ext[1], ".cdg")
        if os.path.exists(cdg_file):
            os.remove(cdg_file)

        self.get_available_songs()

    def rename(self, song_path, new_name):
        logging.info("Renaming song: '" + song_path + "' to: " + new_name)
        ext = os.path.splitext(song_path)
        if len(ext) == 2:
            new_file_name = new_name + ext[1]
        os.rename(song_path, self.download_path + new_file_name)
        # if we have an associated cdg file, rename that too
        cdg_file = song_path.replace(ext[1], ".cdg")
        if os.path.exists(cdg_file):
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
        if "v=" in url:  # accomodates youtube.com/watch?v= and m.youtube.com/?v=
            s = url.split("watch?v=")
        else:  # accomodates youtu.be/
            s = url.split("u.be/")
        if len(s) == 2:
            if "?" in s[1]:  # Strip uneeded Youtube Params
                s[1] = s[1][0 : s[1].index("?")]
            return s[1]
        else:
            logging.error("Error parsing youtube id from url: " + url)
            return None

    def log_ffmpeg_output(self):
        if self.ffmpeg_log != None and self.ffmpeg_log.qsize() > 0:
            while self.ffmpeg_log.qsize() > 0:
                output = self.ffmpeg_log.get_nowait()
                logging.debug("[FFMPEG] " + decode_ignore(output))

    def play_file(self, file_path, semitones=0):
        logging.info(f"Playing file: {file_path} transposed {semitones} semitones")

        requires_transcoding = (
            semitones != 0 or self.normalize_audio or is_transcoding_required(file_path)
        )

        try:
            fr = FileResolver(file_path)
        except Exception as e:
            logging.error("Error resolving file: " + str(e))
            self.queue.pop(0)
            return False

        if self.complete_transcode_before_play or not requires_transcoding:
            # This route is used for streaming the full video file, and includes more
            # accurate headers for safari and other browsers
            stream_url_path = f"/stream/full/{fr.stream_uid}"
        else:
            # This route is used for streaming the video file in chunks, only works on chrome
            stream_url_path = f"/stream/{fr.stream_uid}"

        if not requires_transcoding:
            # simply copy file path to the tmp directory and the stream is ready
            shutil.copy(file_path, fr.output_file)
            max_retries = 5
            while max_retries > 0:
                if os.path.exists(fr.output_file):
                    is_transcoding_complete = True
                    break
                max_retries -= 1
                time.sleep(1)
            if max_retries == 0:
                logging.debug(f"Copying file failed: {fr.output_file}")
        else:
            self.kill_ffmpeg()
            ffmpeg_cmd = build_ffmpeg_cmd(
                fr, semitones, self.normalize_audio, self.complete_transcode_before_play
            )
            self.ffmpeg_process = ffmpeg_cmd.run_async(pipe_stderr=True, pipe_stdin=True)

            # ffmpeg outputs everything useful to stderr for some insane reason!
            # prevent reading stderr from being a blocking action
            self.ffmpeg_log = Queue()
            t = Thread(target=enqueue_output, args=(self.ffmpeg_process.stderr, self.ffmpeg_log))
            t.daemon = True
            t.start()

            output_file_size = 0
            transcode_max_retries = 2500  # Transcode completion max: approx 2 minutes

            is_transcoding_complete = False
            is_buffering_complete = False

            # Transcoding readiness polling loop
            while True:
                self.log_ffmpeg_output()
                # Check if the ffmpeg process has exited
                if self.ffmpeg_process.poll() is not None:
                    exitcode = self.ffmpeg_process.poll()
                    if exitcode != 0:
                        logging.error(
                            f"FFMPEG transcode exited with nonzero exit code ending: {exitcode}. Skipping track"
                        )
                        self.end_song()
                        break
                    else:
                        is_transcoding_complete = True
                        output_file_size = os.path.getsize(fr.output_file)
                        logging.debug(f"Transcoding complete. File size: {output_file_size}")
                        break
                # Check if the file has buffered enough to start playback
                try:
                    output_file_size = os.path.getsize(fr.output_file)
                    if not self.complete_transcode_before_play:
                        is_buffering_complete = output_file_size > self.buffer_size * 1000
                        if is_buffering_complete:
                            logging.debug(f"Buffering complete. File size: {output_file_size}")
                            break
                except:
                    pass
                # Prevent infinite loop if playback never starts
                if transcode_max_retries <= 0:
                    logging.error("Max retries reached trying to play song. Skipping track")
                    self.end_song()
                    break
                transcode_max_retries -= 1
                time.sleep(0.05)

        # Check if the stream is ready to play. Determined by:
        # - completed transcoding
        # - buffered file size being greater than a threshold
        if is_transcoding_complete or is_buffering_complete:
            logging.debug(f"Stream ready!")
            self.now_playing = self.filename_from_path(file_path)
            self.now_playing_filename = file_path
            self.now_playing_transpose = semitones
            self.now_playing_duration = fr.duration
            self.now_playing_url = stream_url_path
            self.now_playing_user = self.queue[0]["user"]
            self.is_paused = False
            self.queue.pop(0)
            # Pause until the stream is playing
            transcode_max_retries = 100
            while self.is_playing == False and transcode_max_retries > 0:
                time.sleep(0.1)  # prevents loop from trying to replay track
                transcode_max_retries -= 1
            if self.is_playing:
                logging.debug("Stream is playing")
            else:
                logging.error(
                    "Stream was not playable! Run with debug logging to see output. Skipping track"
                )
                self.end_song()

    def kill_ffmpeg(self):
        logging.debug("Killing ffmpeg process")
        if self.ffmpeg_process:
            self.ffmpeg_process.kill()

    def start_song(self):
        logging.info(f"Song starting: {self.now_playing}")
        self.is_playing = True

    def end_song(self, reason=None):
        logging.info(f"Song ending: {self.now_playing}")
        if reason != None:
            logging.info(f"Reason: {reason}")
            if reason != "complete":
                # MSG: Message shown when the song ends abnormally
                self.send_message_to_splash(_("Song ended abnormally: %s") % reason, "danger")
        self.reset_now_playing()
        self.kill_ffmpeg()
        delete_tmp_dir()
        logging.debug("ffmpeg process killed")

    def transpose_current(self, semitones):
        # MSG: Message shown after the song is transposed, first is the semitones and then the song name
        self.log_and_send(_("Transposing by %s semitones: %s") % (semitones, self.now_playing))
        # Insert the same song at the top of the queue with transposition
        self.enqueue(self.now_playing_filename, self.now_playing_user, semitones, True)
        self.skip(log_action=False)

    def is_file_playing(self):
        return self.is_playing

    def is_song_in_queue(self, song_path):
        for each in self.queue:
            if each["file"] == song_path:
                return True
        return False

    def is_user_limited(self, user):
        # Returns if a user needs to be limited or not if the limitation is on and if the user reached the limit of songs in queue
        if self.limit_user_songs_by == 0 or user == "Pikaraoke" or user == "Randomizer":
            return False
        cont = len([i for i in self.queue if i["user"] == user]) + (
            1 if self.now_playing_user == user else 0
        )
        return True if cont >= int(self.limit_user_songs_by) else False

    def enqueue(
        self, song_path, user="Pikaraoke", semitones=0, add_to_front=False, log_action=True
    ):
        if self.is_song_in_queue(song_path):
            logging.warning("Song is already in queue, will not add: " + song_path)
            return False
        elif self.is_user_limited(user):
            logging.debug("User limitted by: " + str(self.limit_user_songs_by))
            return [
                False,
                _("You reached the limit of %s song(s) from an user in queue!")
                % (str(self.limit_user_songs_by)),
            ]
        else:
            queue_item = {
                "user": user,
                "file": song_path,
                "title": self.filename_from_path(song_path),
                "semitones": semitones,
            }
            if add_to_front:
                # MSG: Message shown after the song is added to the top of the queue
                self.log_and_send(_("%s added to top of queue: %s") % (user, queue_item["title"]))
                self.queue.insert(0, queue_item)
            else:
                if log_action:
                    # MSG: Message shown after the song is added to the queue
                    self.log_and_send(_("%s added to the queue: %s") % (user, queue_item["title"]))
                self.queue.append(queue_item)
            return [True, _("Song added to the queue: %s") % (self.filename_from_path(song_path))]

    def queue_add_random(self, amount):
        logging.info("Adding %d random songs to queue" % amount)
        songs = list(self.available_songs)  # make a copy
        if len(songs) == 0:
            logging.warning("No available songs!")
            return False
        i = 0
        while i < amount:
            r = random.randint(0, len(songs) - 1)
            if self.is_song_in_queue(songs[r]):
                logging.warning("Song already in queue, trying another... " + songs[r])
            else:
                self.enqueue(songs[r], "Randomizer")
                i += 1
            songs.pop(r)
            if len(songs) == 0:
                logging.warning("Ran out of songs!")
                return False
        return True

    def queue_clear(self):
        # MSG: Message shown after the queue is cleared
        self.log_and_send(_("Clear queue"), "danger")
        self.queue = []
        self.skip(log_action=False)

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
                logging.warning("Song is up next, can't bump up in queue: " + song["file"])
                return False
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                return True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warning("Song is already last, can't bump down in queue: " + song["file"])
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

    def skip(self, log_action=True):
        if self.is_file_playing():
            if log_action:
                # MSG: Message shown after the song is skipped, will be followed by song name
                self.log_and_send(_("Skip: %s") % self.now_playing)
            self.end_song()
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self):
        if self.is_file_playing():
            if self.is_paused:
                # MSG: Message shown after the song is resumed, will be followed by song name
                self.log_and_send(_("Resume: %s") % self.now_playing)
            else:
                # MSG: Message shown after the song is paused, will be followed by song name
                self.log_and_send(_("Pause") + f": {self.now_playing}")
            self.is_paused = not self.is_paused
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def volume_change(self, vol_level):
        self.volume = vol_level
        # MSG: Message shown after the volume is changed, will be followed by the volume level
        self.log_and_send(_("Volume: %s%") % (int(self.volume * 100)))
        return True

    def vol_up(self):
        if self.volume > 1.0:
            new_vol = self.volume = 1.0
            logging.debug("max volume reached.")
        new_vol = self.volume + 0.1
        self.volume_change(new_vol)
        logging.debug(f"Increasing volume by 10%: {self.volume}")

    def vol_down(self):
        if self.volume < 0.1:
            new_vol = self.volume = 0.0
            logging.debug("min volume reached.")
        new_vol = self.volume - 0.1
        self.volume_change(new_vol)
        logging.debug(f"Decreasing volume by 10%: {self.volume}")

    def send_command(self, command):
        # don't allow new messages to clobber existing commands, one message at a time
        # other commands have a higher priority
        if command.startswith("message::") and self.now_playing_command != None:
            return
        self.now_playing_command = command
        threading.Timer(2, self.reset_now_playing_command).start()
        # Clear the command asynchronously. 2s should be enough for client polling to pick it up

    def restart(self):
        if self.is_file_playing():
            self.send_command("restart")
            logging.info("Restarting: " + self.now_playing)
            self.is_paused = False
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self):
        self.running = False

    def handle_run_loop(self):
        time.sleep(self.loop_interval / 1000)

    def reset_now_playing_command(self):
        self.now_playing_command = None

    def reset_now_playing(self):
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.now_playing_url = None
        self.is_paused = True
        self.is_playing = False
        self.now_playing_transpose = 0
        self.now_playing_duration = None
        self.ffmpeg_log = None

    def run(self):
        logging.info("Starting PiKaraoke!")
        logging.info(f"Connect the player host to: {self.url}/splash")
        self.running = True
        while self.running:
            try:
                if not self.is_file_playing() and self.now_playing != None:
                    self.reset_now_playing()
                if len(self.queue) > 0:
                    if not self.is_file_playing():
                        self.reset_now_playing()
                        i = 0
                        while i < (self.splash_delay * 1000):
                            self.handle_run_loop()
                            i += self.loop_interval
                        self.play_file(self.queue[0]["file"], self.queue[0]["semitones"])
                self.log_ffmpeg_output()
                self.handle_run_loop()
            except KeyboardInterrupt:
                logging.warning("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False
