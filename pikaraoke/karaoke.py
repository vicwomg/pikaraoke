import configparser
import contextlib
import hashlib
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import time
from pathlib import Path
from queue import Queue
from subprocess import check_output
from threading import Thread

import qrcode
from flask_babel import _

from pikaraoke.lib.ffmpeg import (
    build_ffmpeg_cmd,
    get_ffmpeg_version,
    is_transpose_enabled,
    supports_hardware_h264_encoding,
)
from pikaraoke.lib.file_resolver import (
    FileResolver,
    delete_tmp_dir,
    is_transcoding_required,
)
from pikaraoke.lib.get_platform import get_os_version, get_platform, is_raspberry_pi
from pikaraoke.lib.youtube_dl import (
    build_ytdl_download_command,
    get_youtube_id_from_url,
    get_youtubedl_version,
    upgrade_youtubedl,
)


# Support function for reading  lines from ffmpeg stderr without blocking
def enqueue_output(out, queue):
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


class Karaoke:
    queue = []
    available_songs = []

    # These all get sent to the /nowplaying endpoint for client-side polling
    now_playing = None
    now_playing_filename = None
    now_playing_user = None
    now_playing_transpose = 0
    now_playing_duration = None
    now_playing_url = None
    now_playing_notification = None
    is_paused = True
    volume = None

    # hashes are used to determine if the client needs to update the now playing or queue
    now_playing_hash = None
    queue_hash = None

    is_playing = False
    process = None
    qr_code_path = None
    base_path = os.path.dirname(__file__)
    loop_interval = 500  # in milliseconds
    default_logo_path = os.path.join(base_path, "logo.png")
    default_bg_music_path = os.path.join(base_path, "static/music/")
    default_bg_video_path = os.path.join(base_path, "static/video/night_sea.mp4")
    screensaver_timeout = 300  # in seconds

    ffmpeg_process = None
    ffmpeg_log = None
    normalize_audio = False

    config_obj = configparser.ConfigParser()

    def __init__(
        self,
        port=5555,
        download_path="/usr/lib/pikaraoke/songs",
        hide_url=False,
        hide_notifications=False,
        hide_splash_screen=False,
        high_quality=False,
        volume=0.85,
        normalize_audio=False,
        complete_transcode_before_play=False,
        buffer_size=150,
        log_level=logging.DEBUG,
        splash_delay=2,
        youtubedl_path="/usr/local/bin/yt-dlp",
        youtubedl_proxy=None,
        logo_path=None,
        hide_overlay=False,
        screensaver_timeout=300,
        url=None,
        prefer_hostname=True,
        disable_bg_music=False,
        bg_music_volume=0.3,
        bg_music_path=None,
        bg_video_path=None,
        disable_bg_video=False,
        disable_score=False,
        limit_user_songs_by=0,
        avsync=0,
        config_file_path="config.ini",
        cdg_pixel_scaling=False,
        additional_ytdl_args=None,
    ):
        logging.basicConfig(
            format="[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=int(log_level),
        )

        # Platform-specific initializations
        self.platform = get_platform()
        self.os_version = get_os_version()
        self.ffmpeg_version = get_ffmpeg_version()
        self.is_transpose_enabled = is_transpose_enabled()
        self.supports_hardware_h264_encoding = supports_hardware_h264_encoding()
        self.youtubedl_version = get_youtubedl_version(youtubedl_path)
        self.is_raspberry_pi = is_raspberry_pi()

        # Initialize variables
        self.config_file_path = config_file_path
        self.port = port
        self.hide_url = self.get_user_preference("hide_url") or hide_url
        self.hide_notifications = (
            self.get_user_preference("hide_notifications") or hide_notifications
        )
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
        self.log_level = log_level
        self.buffer_size = self.get_user_preference("buffer_size") or buffer_size
        self.youtubedl_path = youtubedl_path
        self.youtubedl_proxy = youtubedl_proxy
        self.additional_ytdl_args = additional_ytdl_args
        self.logo_path = self.default_logo_path if logo_path == None else logo_path
        self.hide_overlay = self.get_user_preference("hide_overlay") or hide_overlay
        self.screensaver_timeout = (
            self.get_user_preference("screensaver_timeout") or screensaver_timeout
        )
        self.prefer_hostname = prefer_hostname
        self.disable_bg_music = self.get_user_preference("disable_bg_music") or disable_bg_music
        self.bg_music_volume = self.get_user_preference("bg_music_volume") or bg_music_volume
        self.bg_music_path = self.default_bg_music_path if bg_music_path == None else bg_music_path
        self.disable_bg_video = self.get_user_preference("disable_bg_video") or disable_bg_video
        self.bg_video_path = self.default_bg_video_path if bg_video_path == None else bg_video_path
        self.disable_score = self.get_user_preference("disable_score") or disable_score
        self.limit_user_songs_by = (
            self.get_user_preference("limit_user_songs_by") or limit_user_songs_by
        )
        self.cdg_pixel_scaling = self.get_user_preference("cdg_pixel_scaling") or cdg_pixel_scaling
        self.avsync = self.get_user_preference("avsync") or avsync
        self.url_override = url
        self.url = self.get_url()

        # Log the settings to debug level
        self.log_settings_to_debug()

        # get songs from download_path
        self.get_available_songs()

        self.generate_qr_code()

    def get_url(self):
        if self.is_raspberry_pi:
            # retry in case pi is still starting up
            # and doesn't have an IP yet (occurs when launched from /etc/rc.local)
            end_time = int(time.time()) + 30
            while int(time.time()) < end_time:
                addresses_str = check_output(["hostname", "-I"]).strip().decode("utf-8", "ignore")
                addresses = addresses_str.split(" ")
                self.ip = addresses[0]
                if len(self.ip) < 7:
                    logging.debug("Couldn't get IP, retrying....")
                else:
                    break
        else:
            self.ip = self.get_ip()

        logging.debug("IP address (for QR code and splash screen): " + self.ip)

        if self.url_override != None:
            logging.debug("Overriding URL with " + self.url_override)
            url = self.url_override
        else:
            if self.prefer_hostname:
                url = f"http://{socket.getfqdn().lower()}:{self.port}"
            else:
                url = f"http://{self.ip}:{self.port}"
        return url

    def log_settings_to_debug(self):
        output = ""
        for key, value in sorted(vars(self).items()):
            output += f"  {key}: {value}\n"
        logging.debug("\n\n" + output)

    # def get_user_preferences(self, preference):
    def get_user_preference(self, preference, default_value=False):
        # Try to read the config file
        try:
            self.config_obj.read(self.config_file_path)
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
            setattr(self, preference, val)
            with open(self.config_file_path, "w") as conf:
                self.config_obj.write(conf)
                self.changed_preferences = True
            return [True, _("Your preferences were changed successfully")]
        except Exception as e:
            logging.debug("Failed to change user preference << %s >>: %s", preference, e)
            return [False, _("Something went wrong! Your preferences were not changed")]

    def clear_preferences(self):
        try:
            os.remove(self.config_file_path)
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

    def upgrade_youtubedl(self):
        logging.info("Upgrading youtube-dl, current version: %s" % self.youtubedl_version)
        self.youtubedl_version = upgrade_youtubedl(self.youtubedl_path)
        logging.info("Done. Installed version: %s" % self.youtubedl_version)

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
        yt_search = 'ytsearch%d:"%s"' % (num_results, textToSearch)
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

    def send_notification(self, message, color="primary"):
        # Color should be bulma compatible: primary, warning, success, danger
        if not self.hide_notifications:
            # don't allow new messages to clobber existing commands, one message at a time
            # other commands have a higher priority
            if self.now_playing_notification != None:
                return
            self.now_playing_notification = message + "::is-" + color

    def log_and_send(self, message, category="info"):
        # Category should be one of: info, success, warning, danger
        if category == "success":
            logging.info(message)
            self.send_notification(message, "success")
        elif category == "warning":
            logging.warning(message)
            self.send_notification(message, "warning")
        elif category == "danger":
            logging.error(message)
            self.send_notification(message, "danger")
        else:
            logging.info(message)
            self.send_notification(message, "primary")

    def download_video(self, video_url, enqueue=False, user="Pikaraoke", title=None):
        displayed_title = title if title else video_url
        # MSG: Message shown after the download is started
        self.log_and_send(_("Downloading video: %s" % displayed_title))
        cmd = build_ytdl_download_command(
            self.youtubedl_path,
            video_url,
            self.download_path,
            self.high_quality,
            self.youtubedl_proxy,
            self.additional_ytdl_args,
        )
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
                y = get_youtube_id_from_url(video_url)
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

    def filename_from_path(self, file_path, remove_youtube_id=True):
        rc = os.path.basename(file_path)
        rc = os.path.splitext(rc)[0]
        rc = rc.split("---")[0]  # removes youtube id if present
        if remove_youtube_id:
            try:
                rc = rc.split("---")[0]  # removes youtube id if present
            except TypeError:
                # more fun python 3 hacks
                rc = rc.split("---".encode("utf-8", "ignore"))[0]
        return rc

    def find_song_by_youtube_id(self, youtube_id):
        for each in self.available_songs:
            if youtube_id in each:
                return each
        logging.error("No available song found with youtube id: " + youtube_id)
        return None

    def log_ffmpeg_output(self):
        if self.ffmpeg_log != None and self.ffmpeg_log.qsize() > 0:
            while self.ffmpeg_log.qsize() > 0:
                output = self.ffmpeg_log.get_nowait()
                logging.debug("[FFMPEG] " + output.decode("utf-8", "ignore").strip())

    def play_file(self, file_path, semitones=0):
        logging.info(f"Playing file: {file_path} transposed {semitones} semitones")

        requires_transcoding = (
            semitones != 0
            or self.normalize_audio
            or is_transcoding_required(file_path)
            or self.avsync != 0
        )

        logging.debug(f"Requires transcoding: {requires_transcoding}")

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
                fr,
                semitones,
                self.normalize_audio,
                self.complete_transcode_before_play,
                self.avsync,
                self.cdg_pixel_scaling,
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
            self.update_now_playing_hash()
            self.update_queue_hash()
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
                self.send_notification(_("Song ended abnormally: %s") % reason, "danger")
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
            self.update_queue_hash()
            self.update_now_playing_hash()
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
        self.update_queue_hash()
        self.update_now_playing_hash()
        self.skip(log_action=False)

    def queue_edit(self, song_name, action):
        index = 0
        song = None
        rc = False
        for each in self.queue:
            if song_name in each["file"]:
                song = each
                break
            else:
                index += 1
        if song == None:
            logging.error("Song not found in queue: " + song["file"])
        if action == "up":
            if index < 1:
                logging.warning("Song is up next, can't bump up in queue: " + song["file"])
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                rc = True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warning("Song is already last, can't bump down in queue: " + song["file"])
            else:
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
                rc = True
        elif action == "delete":
            logging.info("Deleting song from queue: " + song["file"])
            del self.queue[index]
            rc = True
        else:
            logging.error("Unrecognized direction: " + action)
        if rc:
            self.update_queue_hash()
            self.update_now_playing_hash()
        return rc

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
            self.update_now_playing_hash()
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def volume_change(self, vol_level):
        self.volume = vol_level
        # MSG: Message shown after the volume is changed, will be followed by the volume level
        self.log_and_send(_("Volume: %s") % (int(self.volume * 100)))
        self.update_now_playing_hash()
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

    def restart(self):
        if self.is_file_playing():
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

    def reset_now_playing_notification(self):
        self.now_playing_notification = None

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
        self.update_now_playing_hash()

    def get_now_playing(self):
        np = {
            "now_playing": self.now_playing,
            "now_playing_user": self.now_playing_user,
            "now_playing_duration": self.now_playing_duration,
            "now_playing_transpose": self.now_playing_transpose,
            "now_playing_url": self.now_playing_url,
            "up_next": self.queue[0]["title"] if len(self.queue) > 0 else None,
            "next_user": self.queue[0]["user"] if len(self.queue) > 0 else None,
            "is_paused": self.is_paused,
            "volume": self.volume,
        }
        return np

    def update_now_playing_hash(self):
        self.now_playing_hash = hashlib.md5(
            json.dumps(self.get_now_playing(), sort_keys=True, ensure_ascii=True).encode(
                "utf-8", "ignore"
            )
        ).hexdigest()

    def update_queue_hash(self):
        self.queue_hash = hashlib.md5(
            json.dumps(self.queue, ensure_ascii=True).encode("utf-8", "ignore")
        ).hexdigest()

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
