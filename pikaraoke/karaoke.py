"""Core karaoke engine for managing songs, queue, and playback."""

from __future__ import annotations

import configparser
import contextlib
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
from typing import Any

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


def enqueue_output(out: Any, queue: Queue) -> None:
    """Read lines from a stream and put them in a queue without blocking.

    Args:
        out: File-like object to read from (e.g., subprocess stderr).
        queue: Queue to put the read lines into.
    """
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


class Karaoke:
    """Main karaoke engine managing songs, queue, and playback.

    This class handles all core karaoke functionality including:
    - Song queue management
    - YouTube video downloading
    - FFmpeg transcoding and playback
    - User preferences
    - QR code generation

    Attributes:
        queue: List of songs in the playback queue.
        available_songs: List of available song file paths.
        now_playing: Title of the currently playing song.
        now_playing_filename: File path of the currently playing song.
        now_playing_user: User who queued the current song.
        now_playing_transpose: Semitones to transpose current song.
        now_playing_duration: Duration of current song in seconds.
        now_playing_url: Stream URL for current song.
        is_paused: Whether playback is paused.
        volume: Current volume level (0.0 to 1.0).
    """

    queue: list[dict[str, Any]] = []
    available_songs: list[str] = []

    # These all get sent to the /nowplaying endpoint for client-side polling
    now_playing: str | None = None
    now_playing_filename: str | None = None
    now_playing_user: str | None = None
    now_playing_transpose: int = 0
    now_playing_duration: int | None = None
    now_playing_url: str | None = None
    now_playing_notification: str | None = None
    is_paused: bool = True
    volume: float | None = None

    # hashes are used to determine if the client needs to update the now playing or queue
    now_playing_hash: str | None = None
    queue_hash: str | None = None

    is_playing: bool = False
    process: subprocess.Popen | None = None
    qr_code_path: str | None = None
    base_path: str = os.path.dirname(__file__)
    loop_interval: int = 500  # in milliseconds
    default_logo_path: str = os.path.join(base_path, "logo.png")
    default_bg_music_path: str = os.path.join(base_path, "static/music/")
    default_bg_video_path: str = os.path.join(base_path, "static/video/night_sea.mp4")
    screensaver_timeout: int = 300  # in seconds

    ffmpeg_process: subprocess.Popen | None = None
    ffmpeg_log: Queue | None = None
    normalize_audio: bool = False

    config_obj: configparser.ConfigParser = configparser.ConfigParser()

    def __init__(
        self,
        port: int = 5555,
        download_path: str = "/usr/lib/pikaraoke/songs",
        hide_url: bool = False,
        hide_notifications: bool = False,
        hide_splash_screen: bool = False,
        high_quality: bool = False,
        volume: float = 0.85,
        normalize_audio: bool = False,
        complete_transcode_before_play: bool = False,
        buffer_size: int = 150,
        log_level: int = logging.DEBUG,
        splash_delay: int = 2,
        youtubedl_path: str = "/usr/local/bin/yt-dlp",
        youtubedl_proxy: str | None = None,
        logo_path: str | None = None,
        hide_overlay: bool = False,
        screensaver_timeout: int = 300,
        url: str | None = None,
        prefer_hostname: bool = True,
        disable_bg_music: bool = False,
        bg_music_volume: float = 0.3,
        bg_music_path: str | None = None,
        bg_video_path: str | None = None,
        disable_bg_video: bool = False,
        disable_score: bool = False,
        limit_user_songs_by: int = 0,
        avsync: float = 0,
        config_file_path: str = "config.ini",
        cdg_pixel_scaling: bool = False,
        streaming_format: str = "hls",
        additional_ytdl_args: str | None = None,
        socketio=None,
    ) -> None:
        """Initialize the Karaoke instance.

        Args:
            port: HTTP server port number.
            download_path: Directory path for downloaded songs.
            hide_url: Hide URL and QR code on splash screen.
            hide_notifications: Disable notification popups.
            hide_splash_screen: Run in headless mode.
            high_quality: Download higher quality videos (up to 1080p).
            volume: Default volume level (0.0 to 1.0).
            normalize_audio: Apply loudness normalization.
            complete_transcode_before_play: Buffer entire file before playback.
            buffer_size: Transcode buffer size in KB.
            log_level: Logging level (e.g., logging.DEBUG).
            splash_delay: Seconds to wait between songs.
            youtubedl_path: Path to yt-dlp executable.
            youtubedl_proxy: Proxy URL for yt-dlp.
            logo_path: Custom logo image path.
            hide_overlay: Hide video overlay.
            screensaver_timeout: Screensaver activation delay in seconds.
            url: Override auto-detected URL.
            prefer_hostname: Use hostname instead of IP in URL.
            disable_bg_music: Disable background music.
            bg_music_volume: Background music volume (0.0 to 1.0).
            bg_music_path: Directory for background music files.
            bg_video_path: Path to background video file.
            disable_bg_video: Disable background video.
            disable_score: Disable score screen.
            limit_user_songs_by: Max songs per user in queue (0 = unlimited).
            avsync: Audio/video sync adjustment in seconds.
            config_file_path: Path to config.ini file.
            cdg_pixel_scaling: Enable CDG pixel scaling.
            streaming_format: Video streaming format ('hls' or 'mp4').
            additional_ytdl_args: Additional yt-dlp command arguments.
            socketio: SocketIO instance for real-time event emission.
        """
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
        self.streaming_format = self.get_user_preference("streaming_format") or streaming_format
        self.socketio = socketio
        self.url_override = url
        self.url = self.get_url()

        # Log the settings to debug level
        self.log_settings_to_debug()

        # get songs from download_path
        self.get_available_songs()

        self.generate_qr_code()

        self.low_score_phrases = self.get_user_preference("low_score_phrases") or ""
        self.mid_score_phrases = self.get_user_preference("mid_score_phrases") or ""
        self.high_score_phrases = self.get_user_preference("high_score_phrases") or ""

    def get_url(self):
        """Get the URL for accessing the PiKaraoke web interface.

        On Raspberry Pi, retries getting the IP address for up to 30 seconds
        in case the network is still initializing at startup.

        Returns:
            URL string in format http://ip:port
        """
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

    def log_settings_to_debug(self) -> None:
        """Log all current settings at debug level."""
        output = ""
        for key, value in sorted(vars(self).items()):
            output += f"  {key}: {value}\n"
        logging.debug("\n\n" + output)

    def get_user_preference(self, preference: str, default_value: Any = False) -> Any:
        """Get a user preference from the config file.

        Args:
            preference: Name of the preference to retrieve.
            default_value: Value to return if preference not found.

        Returns:
            The preference value (auto-converted to bool/int/float if applicable).
        """
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

    def change_preferences(self, preference: str, val: Any) -> list[bool | str]:
        """Update a user preference in the config file.

        Args:
            preference: Name of the preference to change.
            val: New value for the preference.

        Returns:
            List of [success: bool, message: str].
        """

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

    def clear_preferences(self) -> list[bool | str]:
        """Remove all user preferences by deleting the config file.

        Returns:
            List of [success: bool, message: str].
        """
        try:
            os.remove(self.config_file_path)
            return [True, _("Your preferences were cleared successfully")]
        except OSError:
            return [False, _("Something went wrong! Your preferences were not cleared")]

    def get_ip(self) -> str:
        """Get the local IP address of this machine.

        Returns:
            IP address string.
        """
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

    def upgrade_youtubedl(self) -> None:
        """Upgrade yt-dlp to the latest version."""
        logging.info("Upgrading youtube-dl, current version: %s" % self.youtubedl_version)
        self.youtubedl_version = upgrade_youtubedl(self.youtubedl_path)
        logging.info("Done. Installed version: %s" % self.youtubedl_version)

    def generate_qr_code(self) -> None:
        """Generate a QR code image for the web interface URL."""
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

    def get_search_results(self, textToSearch: str) -> list[list[str]]:
        """Search YouTube for videos matching the query.

        Args:
            textToSearch: Search query string.

        Returns:
            List of [title, url, video_id] for each result.

        Raises:
            Exception: If the search fails.
        """
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

    def get_karaoke_search_results(self, songTitle: str) -> list[list[str]]:
        """Search YouTube for karaoke versions of a song.

        Args:
            songTitle: Song title to search for.

        Returns:
            List of [title, url, video_id] for each result.
        """
        return self.get_search_results(songTitle + " karaoke")

    def send_notification(self, message: str, color: str = "primary") -> None:
        """Send a notification to the web interface.

        Args:
            message: Notification message text.
            color: Bulma color class (primary, warning, success, danger).
        """
        # Color should be bulma compatible: primary, warning, success, danger
        if not self.hide_notifications:
            # don't allow new messages to clobber existing commands, one message at a time
            # other commands have a higher priority
            if self.now_playing_notification != None:
                return
            self.now_playing_notification = message + "::is-" + color
            # Emit notification via SocketIO for event-driven architecture
            if self.socketio:
                self.socketio.emit("notification", self.now_playing_notification, namespace="/")

    def log_and_send(self, message: str, category: str = "info") -> None:
        """Log a message and send it as a notification.

        Args:
            message: Message to log and display.
            category: Message category (info, success, warning, danger).
        """
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

    def download_video(
        self,
        video_url: str,
        enqueue: bool = False,
        user: str = "Pikaraoke",
        title: str | None = None,
    ) -> int:
        """Download a video from YouTube.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).

        Returns:
            Return code from the download process (0 = success).
        """
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

    def get_available_songs(self) -> None:
        """Scan the download directory and update the available songs list."""
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

    def delete(self, song_path: str) -> None:
        """Delete a song file and its associated CDG file if present.

        Args:
            song_path: Full path to the song file.
        """
        logging.info("Deleting song: " + song_path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(song_path)
        ext = os.path.splitext(song_path)
        # if we have an associated cdg file, delete that too
        cdg_file = song_path.replace(ext[1], ".cdg")
        if os.path.exists(cdg_file):
            os.remove(cdg_file)

        self.get_available_songs()

    def rename(self, song_path: str, new_name: str) -> None:
        """Rename a song file and its associated CDG file if present.

        Args:
            song_path: Full path to the current song file.
            new_name: New filename (without extension).
        """
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

    def filename_from_path(self, file_path: str, remove_youtube_id: bool = True) -> str:
        """Extract a clean display name from a file path.

        Args:
            file_path: Full path to the file.
            remove_youtube_id: Strip YouTube ID suffix if present.

        Returns:
            Clean filename without extension or YouTube ID.
        """
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

    def find_song_by_youtube_id(self, youtube_id: str) -> str | None:
        """Find a song in available_songs by its YouTube ID.

        Args:
            youtube_id: YouTube video ID to search for.

        Returns:
            Full path to the song file, or None if not found.
        """
        for each in self.available_songs:
            if youtube_id in each:
                return each
        logging.error("No available song found with youtube id: " + youtube_id)
        return None

    def log_ffmpeg_output(self) -> None:
        """Log any pending FFmpeg output from the queue."""
        if self.ffmpeg_log != None and self.ffmpeg_log.qsize() > 0:
            while self.ffmpeg_log.qsize() > 0:
                output = self.ffmpeg_log.get_nowait()
                logging.debug("[FFMPEG] " + output.decode("utf-8", "ignore").strip())

    def play_file(self, file_path: str, semitones: int = 0) -> bool | None:
        """Start playback of a media file.

        Handles file resolution, transcoding, and stream setup.

        Args:
            file_path: Path to the media file to play.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            False if file resolution fails, None otherwise.
        """
        logging.info(f"Playing file: {file_path} transposed {semitones} semitones")

        is_hls = self.streaming_format == "hls"

        requires_transcoding = (
            semitones != 0
            or self.normalize_audio
            or is_transcoding_required(file_path)
            or self.avsync != 0
            or is_hls
        )

        logging.debug(f"Requires transcoding: {requires_transcoding}")

        try:
            fr = FileResolver(file_path, self.streaming_format)
        except Exception as e:
            error_message = _("Error resolving file: %s") % str(e)
            self.queue.pop(0)
            self.end_song(reason=error_message)
            self.log_and_send(error_message, "danger")
            return False

        # Set stream URL based on format
        if is_hls:
            stream_url_path = f"/stream/{fr.stream_uid}.m3u8"
        else:
            stream_url_path = f"/stream/{fr.stream_uid}.mp4"

        # For non-transcoded or complete transcoding files, use direct MP4 serving (except in HLS mode)
        if (self.complete_transcode_before_play or not requires_transcoding) and not is_hls:
            stream_url_path = f"/stream/full/{fr.stream_uid}"

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
            # HLS mode will override the complete_transcode_before_play setting
            force_mp4_encoding = False if is_hls else self.complete_transcode_before_play
            ffmpeg_cmd = build_ffmpeg_cmd(
                fr,
                semitones,
                self.normalize_audio,
                force_mp4_encoding,
                self.avsync,
                self.cdg_pixel_scaling,
            )
            self.ffmpeg_process = ffmpeg_cmd.run_async(pipe_stderr=True, pipe_stdin=True)

            # ffmpeg outputs everything useful to stderr for some insane reason!
            # prevent reading stderr from being a blocking action
            self.ffmpeg_log = Queue()
            t = Thread(
                target=enqueue_output,
                args=(self.ffmpeg_process.stderr, self.ffmpeg_log),
            )
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
                        # Both mp4 and hls modes now use HLS format (init.mp4 + segments)
                        # Check if playlist has at least 2 segments ready for streaming
                        segment_count = 0  # Initialize to avoid NameError in logging
                        if output_file_size > 0:
                            with open(fr.output_file, "r") as f:
                                playlist_content = f.read()
                                segment_count = playlist_content.count(".m4s")
                                # Convert buffer_size (KB) to segment count
                                # Each segment ≈ 3 seconds at configured bitrate (roughly 600-800KB)
                                min_segments = max(2, int(self.buffer_size / 700))
                                is_buffering_complete = segment_count >= min_segments
                        if is_buffering_complete:
                            logging.debug(
                                f"Buffering complete. Playlist size: {output_file_size}, Segments: {segment_count}"
                            )
                            break
                except FileNotFoundError:
                    # Expected: FFmpeg hasn't created the playlist file yet
                    # Continue waiting in the loop
                    pass
                except (PermissionError, OSError, IOError) as e:
                    # Unexpected: SD card issues, filesystem problems
                    logging.warning(
                        f"I/O error while checking buffer status for {fr.output_file}: {e}"
                    )
                    # Continue - may be transient issue
                except UnicodeDecodeError as e:
                    # FFmpeg wrote binary data instead of text playlist - error state
                    logging.error(f"Failed to read playlist as text (FFmpeg may have errored): {e}")
                    # Continue for now, but this is suspicious
                except Exception as e:
                    # Catch-all for truly unexpected errors
                    logging.error(
                        f"Unexpected error during buffering check: {type(e).__name__}: {e}"
                    )
                    # Continue waiting, don't crash
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

    def kill_ffmpeg(self) -> None:
        """Terminate the running FFmpeg process gracefully.

        Uses SIGTERM first to allow FFmpeg to clean up resources properly,
        then SIGKILL if process doesn't terminate within timeout.
        This is critical for Raspberry Pi to release GPU memory from h264_v4l2m2m encoder.
        """
        if self.ffmpeg_process:
            logging.debug("Terminating ffmpeg process gracefully")
            try:
                # First attempt: graceful termination (SIGTERM)
                self.ffmpeg_process.terminate()
                # Wait up to 5 seconds for graceful shutdown
                self.ffmpeg_process.wait(timeout=5)
                logging.debug("FFmpeg process terminated gracefully")
            except subprocess.TimeoutExpired:
                # Process didn't terminate, force kill (SIGKILL)
                logging.warning("FFmpeg did not terminate gracefully, forcing kill")
                self.ffmpeg_process.kill()
                # Ensure process is fully dead and reaped (prevents zombies)
                self.ffmpeg_process.wait()
                logging.debug("FFmpeg process force killed")
            except Exception as e:
                # Catch any other errors (process already dead, etc.)
                logging.debug(f"FFmpeg termination exception: {e}")
            finally:
                self.ffmpeg_process = None

    def start_song(self) -> None:
        """Mark the current song as actively playing."""
        logging.info(f"Song starting: {self.now_playing}")
        self.is_playing = True

    def end_song(self, reason: str | None = None) -> None:
        """End the current song and clean up resources.

        Args:
            reason: Optional reason for ending (e.g., 'complete', 'skip').
        """
        logging.info(f"Song ending: {self.now_playing}")
        if reason != None:
            logging.info(f"Reason: {reason}")
            if reason != "complete":
                # MSG: Message shown when the song ends abnormally
                self.send_notification(_("Song ended abnormally: %s") % reason, "danger")
        self.reset_now_playing()
        self.kill_ffmpeg()
        # Small delay to ensure FFmpeg fully terminates and file handles close
        # Critical on Raspberry Pi with slow SD cards and hardware encoder cleanup
        time.sleep(0.3)
        delete_tmp_dir()
        logging.debug("Cleanup complete")

    def transpose_current(self, semitones: int) -> None:
        """Restart the current song with a new transpose value.

        Args:
            semitones: Number of semitones to transpose.
        """
        # MSG: Message shown after the song is transposed, first is the semitones and then the song name
        self.log_and_send(_("Transposing by %s semitones: %s") % (semitones, self.now_playing))
        # Insert the same song at the top of the queue with transposition
        self.enqueue(self.now_playing_filename, self.now_playing_user, semitones, True)
        self.skip(log_action=False)

    def is_file_playing(self) -> bool:
        """Check if a file is currently playing.

        Returns:
            True if a song is playing, False otherwise.
        """
        return self.is_playing

    def is_song_in_queue(self, song_path: str) -> bool:
        """Check if a song is already in the queue.

        Args:
            song_path: Path to the song file.

        Returns:
            True if the song is in the queue.
        """
        for each in self.queue:
            if each["file"] == song_path:
                return True
        return False

    def is_user_limited(self, user: str) -> bool:
        """Check if a user has reached their queue limit.

        Args:
            user: Username to check.

        Returns:
            True if the user has reached their song limit.
        """
        # Returns if a user needs to be limited or not if the limitation is on and if the user reached the limit of songs in queue
        if self.limit_user_songs_by == 0 or user == "Pikaraoke" or user == "Randomizer":
            return False
        cont = len([i for i in self.queue if i["user"] == user]) + (
            1 if self.now_playing_user == user else 0
        )
        return True if cont >= int(self.limit_user_songs_by) else False

    def enqueue(
        self,
        song_path: str,
        user: str = "Pikaraoke",
        semitones: int = 0,
        add_to_front: bool = False,
        log_action: bool = True,
    ) -> bool | list[bool | str]:
        """Add a song to the queue.

        Args:
            song_path: Path to the song file.
            user: Username adding the song.
            semitones: Transpose value for playback.
            add_to_front: If True, add to front of queue instead of back.
            log_action: Whether to log and notify about the action.

        Returns:
            False if song already in queue, or list of [success, message].
        """
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
            return [
                True,
                _("Song added to the queue: %s") % (self.filename_from_path(song_path)),
            ]

    def queue_add_random(self, amount: int) -> bool:
        """Add random songs to the queue.

        Args:
            amount: Number of random songs to add.

        Returns:
            True if successful, False if ran out of songs.
        """
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

    def queue_clear(self) -> None:
        """Clear all songs from the queue and skip current song."""
        # MSG: Message shown after the queue is cleared
        self.log_and_send(_("Clear queue"), "danger")
        self.queue = []
        self.update_queue_hash()
        self.update_now_playing_hash()
        self.skip(log_action=False)

    def queue_edit(self, song_name: str, action: str) -> bool:
        """Edit the queue by moving or removing a song.

        Args:
            song_name: Name/path of the song to edit.
            action: Action to perform ('up', 'down', 'delete').

        Returns:
            True if the action was successful.
        """
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

    def skip(self, log_action: bool = True) -> bool:
        """Skip the currently playing song.

        Args:
            log_action: Whether to log and notify about the skip.

        Returns:
            True if a song was skipped, False if nothing playing.
        """
        if self.is_file_playing():
            if log_action:
                # MSG: Message shown after the song is skipped, will be followed by song name
                self.log_and_send(_("Skip: %s") % self.now_playing)
            self.end_song()
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self) -> bool:
        """Toggle pause state of the current song.

        Returns:
            True if successful, False if nothing playing.
        """
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

    def volume_change(self, vol_level: float) -> bool:
        """Set the volume level.

        Args:
            vol_level: Volume level (0.0 to 1.0).

        Returns:
            True after setting volume.
        """
        self.volume = vol_level
        # MSG: Message shown after the volume is changed, will be followed by the volume level
        self.log_and_send(_("Volume: %s") % (int(self.volume * 100)))
        self.update_now_playing_hash()
        return True

    def vol_up(self) -> None:
        """Increase volume by 10%."""
        if self.volume > 1.0:
            new_vol = self.volume = 1.0
            logging.debug("max volume reached.")
        new_vol = self.volume + 0.1
        self.volume_change(new_vol)
        logging.debug(f"Increasing volume by 10%: {self.volume}")

    def vol_down(self) -> None:
        """Decrease volume by 10%."""
        if self.volume < 0.1:
            new_vol = self.volume = 0.0
            logging.debug("min volume reached.")
        new_vol = self.volume - 0.1
        self.volume_change(new_vol)
        logging.debug(f"Decreasing volume by 10%: {self.volume}")

    def restart(self) -> bool:
        """Restart the current song from the beginning.

        Returns:
            True if successful, False if nothing playing.
        """
        if self.is_file_playing():
            logging.info("Restarting: " + self.now_playing)
            self.is_paused = False
            self.update_now_playing_hash()
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self) -> None:
        """Stop the karaoke run loop."""
        self.running = False

    def handle_run_loop(self) -> None:
        """Handle one iteration of the main run loop with a sleep interval."""
        time.sleep(self.loop_interval / 1000)

    def reset_now_playing_notification(self) -> None:
        """Clear the current notification."""
        self.now_playing_notification = None

    def reset_now_playing(self) -> None:
        """Reset all now playing state to defaults."""
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

    def get_now_playing(self) -> dict[str, Any]:
        """Get the current playback state.

        Returns:
            Dictionary with now playing info, queue preview, and volume.
        """
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

    def update_now_playing_hash(self) -> None:
        """Emit now_playing state change via SocketIO."""
        if self.socketio:
            self.socketio.emit("now_playing", self.get_now_playing(), namespace="/")

    def update_queue_hash(self) -> None:
        """Emit queue_update state change via SocketIO."""
        if self.socketio:
            self.socketio.emit("queue_update", namespace="/")

    def run(self) -> None:
        """Main run loop - processes queue and plays songs.

        This method blocks until stop() is called or KeyboardInterrupt.
        """
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
