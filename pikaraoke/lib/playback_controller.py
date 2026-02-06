"""Playback controller for managing video playback state and coordination."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from flask_babel import _

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.file_resolver import delete_tmp_dir
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.stream_manager import PlaybackResult, StreamManager

if TYPE_CHECKING:
    import subprocess


class PlaybackController:
    """Controller for managing playback state and stream coordination.

    Owns all "now playing" state and coordinates with StreamManager for
    FFmpeg transcoding and playback.

    Attributes:
        now_playing: Title of the currently playing song.
        now_playing_filename: File path of the currently playing song.
        now_playing_user: User who queued the current song.
        now_playing_transpose: Semitones to transpose current song.
        now_playing_duration: Duration of current song in seconds.
        now_playing_url: Stream URL for current song.
        now_playing_subtitle_url: URL path for subtitles.
        now_playing_position: Current playback position in seconds.
        is_paused: Whether playback is paused.
        is_playing: Whether a song is currently playing.
        ffmpeg_process: Currently running FFmpeg subprocess.
    """

    now_playing: str | None = None
    now_playing_filename: str | None = None
    now_playing_user: str | None = None
    now_playing_transpose: int = 0
    now_playing_duration: int | None = None
    now_playing_url: str | None = None
    now_playing_subtitle_url: str | None = None
    now_playing_position: float | None = None
    is_paused: bool = True
    is_playing: bool = False

    def __init__(
        self,
        preferences: PreferenceManager,
        events: EventSystem,
        filename_from_path: Callable[[str, bool], str],
        streaming_format: str = "hls",
    ) -> None:
        """Initialize the playback controller.

        Args:
            preferences: PreferenceManager instance for configuration.
            events: EventSystem instance for event emission.
            filename_from_path: Function to extract display name from path.
            streaming_format: Video streaming format ('hls' or 'mp4').
        """
        self.preferences = preferences
        self.events = events
        self.filename_from_path = filename_from_path
        self.stream_manager = StreamManager(preferences, streaming_format)

    @property
    def ffmpeg_process(self) -> subprocess.Popen | None:
        """Get the current FFmpeg process."""
        return self.stream_manager.ffmpeg_process

    def play_file(self, file_path: str, user: str, semitones: int = 0) -> PlaybackResult:
        """Start playback of a media file.

        Blocks until client connects or timeout occurs.

        Args:
            file_path: Path to the media file to play.
            user: User who queued the song.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            PlaybackResult with success status and stream information.
        """
        logging.info(
            f"Playing file: {file_path} for user: {user}, transposed {semitones} semitones"
        )

        result = self.stream_manager.play_file(file_path, semitones)

        if not result.success:
            return result

        self.now_playing = self.filename_from_path(file_path, remove_youtube_id=True)
        self.now_playing_filename = file_path
        self.now_playing_user = user
        self.now_playing_transpose = semitones
        self.now_playing_duration = result.duration
        self.now_playing_url = result.stream_url
        self.now_playing_subtitle_url = result.subtitle_url
        self.is_paused = False

        self.events.emit("playback_started")

        # Wait for client to connect
        max_retries = 100
        while not self.is_playing and max_retries > 0:
            time.sleep(0.1)
            max_retries -= 1

        if not self.is_playing:
            error_msg = _("Stream was not playable! Skipping track")
            logging.error(error_msg)
            self.end_song(reason="timeout")
            return PlaybackResult(success=False, error=error_msg)

        logging.debug("Stream is playing")
        return result

    def start_song(self) -> None:
        """Mark the current song as actively playing.

        Called by Flask route when client connects to stream.
        """
        logging.info(f"Song starting: {self.now_playing}")
        self.is_playing = True

    def end_song(self, reason: str | None = None) -> None:
        """End the current song and clean up resources.

        Args:
            reason: Optional reason for ending (e.g., 'complete', 'skip', 'timeout').
        """
        logging.info(f"Song ending: {self.now_playing}")
        if reason:
            logging.info(f"Reason: {reason}")
            if reason not in ("complete", "skip"):
                # MSG: Message shown when the song ends abnormally
                self.events.emit("notification", _("Song ended abnormally: %s") % reason, "danger")

        self.reset_now_playing()
        self.stream_manager.kill_ffmpeg()
        # Small delay to ensure FFmpeg fully terminates and file handles close
        # Critical on Raspberry Pi with slow SD cards and hardware encoder cleanup
        time.sleep(0.3)
        delete_tmp_dir()
        logging.debug("Cleanup complete")

        self.events.emit("song_ended")

    def skip(self, log_action: bool = True) -> bool:
        """Skip the currently playing song.

        Args:
            log_action: Whether to log and notify about the skip.

        Returns:
            True if a song was skipped, False if nothing playing.
        """
        if self.is_playing:
            if log_action:
                # MSG: Message shown after the song is skipped, will be followed by song name
                self.events.emit("notification", _("Skip: %s") % self.now_playing, "info")
            self.end_song(reason="skip")
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self) -> bool:
        """Toggle pause state of the current song.

        Returns:
            True if successful, False if nothing playing.
        """
        if self.is_playing:
            if self.is_paused:
                # MSG: Message shown after the song is resumed, will be followed by song name
                self.events.emit("notification", _("Resume: %s") % self.now_playing, "info")
            else:
                # MSG: Message shown after the song is paused, will be followed by song name
                self.events.emit("notification", _("Pause: %s") % self.now_playing, "info")
            self.is_paused = not self.is_paused
            self.events.emit("now_playing_update")
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def get_now_playing(self) -> dict[str, str | int | float | bool | None]:
        """Get the current playback state.

        Returns:
            Dictionary with now playing information.
        """
        return {
            "now_playing": self.now_playing,
            "now_playing_user": self.now_playing_user,
            "now_playing_duration": self.now_playing_duration,
            "now_playing_transpose": self.now_playing_transpose,
            "now_playing_url": self.now_playing_url,
            "now_playing_subtitle_url": self.now_playing_subtitle_url,
            "now_playing_position": self.now_playing_position,
            "is_paused": self.is_paused,
        }

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

    def log_output(self) -> None:
        """Log any pending FFmpeg output."""
        self.stream_manager.log_ffmpeg_output()
