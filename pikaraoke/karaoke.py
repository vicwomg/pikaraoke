"""Core karaoke engine for managing songs, queue, and playback."""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
import time
from subprocess import check_output
from typing import Any

import qrcode
from flask_babel import _
from qrcode.image.pure import PyPNGImage

from pikaraoke.lib.download_manager import DownloadManager
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import (
    get_ffmpeg_version,
    is_transpose_enabled,
    supports_hardware_h264_encoding,
)
from pikaraoke.lib.file_resolver import delete_tmp_dir
from pikaraoke.lib.get_platform import (
    get_data_directory,
    get_os_version,
    get_platform,
    is_raspberry_pi,
)
from pikaraoke.lib.network import get_ip
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_list import SongList
from pikaraoke.lib.stream_manager import StreamManager
from pikaraoke.lib.youtube_dl import (
    get_search_results,
    get_youtubedl_version,
    upgrade_youtubedl,
)
from pikaraoke.version import __version__ as VERSION


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

    available_songs: SongList
    queue_manager: QueueManager

    # These all get sent to the /nowplaying endpoint for client-side polling
    now_playing: str | None = None
    now_playing_filename: str | None = None
    now_playing_user: str | None = None
    now_playing_transpose: int = 0
    now_playing_duration: int | None = None
    now_playing_url: str | None = None
    now_playing_subtitle_url: str | None = None
    now_playing_notification: str | None = None
    now_playing_position: float | None = None
    is_paused: bool = True
    volume: float

    is_playing: bool = False
    process: subprocess.Popen | None = None
    qr_code_path: str | None = None
    base_path: str = os.path.dirname(__file__)
    loop_interval: int = 500  # in milliseconds
    default_logo_path: str = os.path.join(base_path, "static", "images", "logo.png")
    default_bg_music_path: str = os.path.join(base_path, "static", "music")
    default_bg_video_path: str = os.path.join(base_path, "static", "video", "night_sea.mp4")
    screensaver_timeout: int

    normalize_audio: bool

    # Download manager for serialized downloads
    download_manager: DownloadManager

    # Event system and preferences
    events: EventSystem
    preferences: PreferenceManager

    def __init__(
        self,
        port: int = 5555,
        download_path: str = "/usr/lib/pikaraoke/songs",
        hide_url: bool | None = None,
        hide_notifications: bool | None = None,
        hide_splash_screen: bool | None = None,
        high_quality: bool | None = None,
        volume: float | None = None,
        normalize_audio: bool | None = None,
        complete_transcode_before_play: bool | None = None,
        buffer_size: int | None = None,
        log_level: int = logging.DEBUG,
        splash_delay: int | None = None,
        youtubedl_proxy: str | None = None,
        logo_path: str | None = None,
        hide_overlay: bool | None = None,
        screensaver_timeout: int | None = None,
        url: str | None = None,
        prefer_hostname: bool | None = None,
        disable_bg_music: bool | None = None,
        bg_music_volume: float | None = None,
        bg_music_path: str | None = None,
        bg_video_path: str | None = None,
        disable_bg_video: bool | None = None,
        disable_score: bool | None = None,
        limit_user_songs_by: int | None = None,
        avsync: float | None = None,
        config_file_path: str = "config.ini",
        cdg_pixel_scaling: bool | None = None,
        streaming_format: str | None = None,
        browse_results_per_page: int | None = None,
        additional_ytdl_args: str | None = None,
        socketio=None,
        preferred_language: str | None = None,
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
            browse_results_per_page: Number of search results per page.
            additional_ytdl_args: Additional yt-dlp command arguments.
            socketio: SocketIO instance for real-time event emission.
            preferred_language: Language code for UI (e.g., 'en', 'de_DE').
        """
        logging.basicConfig(
            format="[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=int(log_level),
        )

        # Initialize event system and preferences (foundation for all components)
        self.events = EventSystem()
        self.preferences = PreferenceManager(config_file_path, target=self)

        # Platform-specific initializations
        self.platform = get_platform()
        self.os_version = get_os_version()
        self.ffmpeg_version = get_ffmpeg_version()
        self.is_transpose_enabled = is_transpose_enabled()
        self.supports_hardware_h264_encoding = supports_hardware_h264_encoding()
        self.youtubedl_version = get_youtubedl_version()
        self.is_raspberry_pi = is_raspberry_pi()

        logging.info("PiKaraoke version: " + VERSION)

        # Set non-preference attributes (not stored in config)
        self.port = port
        self.hide_splash_screen = hide_splash_screen
        self.download_path = download_path
        self.log_level = log_level
        self.youtubedl_proxy = youtubedl_proxy
        self.additional_ytdl_args = additional_ytdl_args
        self.logo_path = self.default_logo_path if logo_path == None else logo_path
        self.prefer_hostname = prefer_hostname
        self.bg_music_path = self.default_bg_music_path if bg_music_path == None else bg_music_path
        self.bg_video_path = self.default_bg_video_path if bg_video_path == None else bg_video_path
        self.socketio = socketio
        self.url_override = url
        self.url = self.get_url()

        # Load all preference-driven attributes from config (with CLI overrides as fallback)
        cli_args = {k: v for k, v in locals().items() if k != "self"}
        self._load_preferences(**cli_args)

        # Log the settings to debug level
        self.log_settings_to_debug()

        # Initialize song list and load songs from download_path
        self.available_songs = SongList()
        self.get_available_songs()

        self.generate_qr_code()

        # Set preferred language from command line if provided (persists to config)
        if preferred_language:
            self.preferences.set("preferred_language", preferred_language)
            logging.info(f"Setting preferred language to: {preferred_language}")

        # Initialize and start download manager
        self.download_manager = DownloadManager(self)
        self.download_manager.start()

        # Initialize stream manager for transcoding and playback
        self.stream_manager = StreamManager(self)

        # Initialize queue manager
        self.queue_manager = QueueManager(
            socketio=socketio,
            get_limit_user_songs_by=lambda: self.limit_user_songs_by,
            get_enable_fair_queue=lambda: self.enable_fair_queue,
            get_now_playing_user=lambda: self.now_playing_user,
            filename_from_path=self.filename_from_path,
            log_and_send=self.log_and_send,
            get_available_songs=lambda: self.available_songs,
            update_now_playing_socket=self.update_now_playing_socket,
            skip=self.skip,
        )

    def _load_preferences(self, **cli_overrides: Any) -> None:
        """Load preference-driven attributes from config file.

        Priority: CLI argument (if provided) > config file > PreferenceManager.DEFAULTS
        """
        self.preferences.apply_all(**cli_overrides)

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
            self.ip = get_ip(self.platform)

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

    def upgrade_youtubedl(self) -> None:
        """Upgrade yt-dlp to the latest version."""
        logging.debug(
            "Checking if youtube-dl needs upgrading, current version: %s" % self.youtubedl_version
        )
        self.youtubedl_version = upgrade_youtubedl()

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
        img = qr.make_image(image_factory=PyPNGImage)
        # Use writable data directory instead of program directory
        data_dir = get_data_directory()
        self.qr_code_path = os.path.join(data_dir, "qrcode.png")
        img.save(self.qr_code_path)  # type: ignore[arg-type]

    def get_search_results(self, textToSearch: str) -> list[list[str]]:
        """Search YouTube for videos matching the query.

        Args:
            textToSearch: Search query string.

        Returns:
            List of [title, url, video_id] for each result.
        """
        return get_search_results(textToSearch)

    def get_karaoke_search_results(self, songTitle: str) -> list[list[str]]:
        """Search YouTube for karaoke versions of a song.

        Args:
            songTitle: Song title to search for.

        Returns:
            List of [title, url, video_id] for each result.
        """
        return get_search_results(songTitle + " karaoke")

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
    ) -> None:
        """Queue a video for download from YouTube.

        Downloads are processed serially to prevent rate limiting and CPU overload.
        A notification is sent when the download is queued, and another when it starts.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to playback queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).
        """
        self.download_manager.queue_download(video_url, enqueue, user, title)

    def get_available_songs(self) -> None:
        """Scan the download directory and update the available songs list."""
        self.available_songs.scan_directory(self.download_path)

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

        self.available_songs.remove(song_path)

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
        else:
            new_file_name = new_name
        new_path = self.download_path + new_file_name
        os.rename(song_path, new_path)
        # if we have an associated cdg file, rename that too
        cdg_file = song_path.replace(ext[1], ".cdg")
        if os.path.exists(cdg_file):
            os.rename(cdg_file, self.download_path + new_name + ".cdg")
        self.available_songs.rename(song_path, new_path)

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
        if remove_youtube_id:
            rc = rc.split("---")[0]  # removes youtube id if present
        return rc

    def play_file(self, file_path: str, semitones: int = 0) -> bool | None:
        """Start playback of a media file.

        Delegates to StreamManager for transcoding and stream setup.

        Args:
            file_path: Path to the media file to play.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            False if file resolution fails, None otherwise.
        """
        return self.stream_manager.play_file(file_path, semitones)

    def kill_ffmpeg(self) -> None:
        """Terminate the running FFmpeg process gracefully."""
        self.stream_manager.kill_ffmpeg()

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
        if self.now_playing_filename is None or self.now_playing_user is None:
            logging.warning("Cannot transpose: no song currently playing")
            return
        # MSG: Message shown after the song is transposed, first is the semitones and then the song name
        self.log_and_send(_("Transposing by %s semitones: %s") % (semitones, self.now_playing))
        # Insert the same song at the top of the queue with transposition
        self.queue_manager.enqueue(
            self.now_playing_filename, self.now_playing_user, semitones, True
        )
        self.skip(log_action=False)

    def is_file_playing(self) -> bool:
        """Check if a file is currently playing.

        Returns:
            True if a song is playing, False otherwise.
        """
        return self.is_playing

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
            self.update_now_playing_socket()
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
        self.update_now_playing_socket()
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
            logging.info("Restarting: " + (self.now_playing or "unknown song"))
            self.is_paused = False
            self.update_now_playing_socket()
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
        self.now_playing_subtitle_url = None
        self.is_paused = True
        self.is_playing = False
        self.now_playing_transpose = 0
        self.now_playing_duration = None
        self.now_playing_position = None
        self.update_now_playing_socket()

    def get_now_playing(self) -> dict[str, Any]:
        """Get the current playback state.

        Returns:
            Dictionary with now playing info, queue preview, and volume.
        """
        queue = self.queue_manager.queue
        next_song = queue[0] if queue else None
        return {
            "now_playing": self.now_playing,
            "now_playing_user": self.now_playing_user,
            "now_playing_duration": self.now_playing_duration,
            "now_playing_transpose": self.now_playing_transpose,
            "now_playing_url": self.now_playing_url,
            "now_playing_subtitle_url": self.now_playing_subtitle_url,
            "now_playing_position": self.now_playing_position,
            "up_next": next_song["title"] if next_song else None,
            "next_user": next_song["user"] if next_song else None,
            "is_paused": self.is_paused,
            "volume": self.volume,
        }

    def update_now_playing_socket(self) -> None:
        """Emit now_playing state change via SocketIO."""
        if self.socketio:
            self.socketio.emit("now_playing", self.get_now_playing(), namespace="/")

    def run(self) -> None:
        """Main run loop - processes queue and plays songs.

        This method blocks until stop() is called or KeyboardInterrupt.
        """
        logging.debug("Starting PiKaraoke run loop")
        logging.info(f"Connect the player host to: {self.url}/splash")
        self.running = True
        while self.running:
            try:
                if not self.is_file_playing() and self.now_playing != None:
                    self.reset_now_playing()
                if len(self.queue_manager.queue) > 0:
                    if not self.is_file_playing():
                        self.reset_now_playing()
                        i = 0
                        while i < (self.splash_delay * 1000):
                            self.handle_run_loop()
                            i += self.loop_interval
                        self.play_file(
                            self.queue_manager.queue[0]["file"],
                            self.queue_manager.queue[0]["semitones"],
                        )
                self.stream_manager.log_ffmpeg_output()
                self.handle_run_loop()
            except KeyboardInterrupt:
                logging.warning("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False
