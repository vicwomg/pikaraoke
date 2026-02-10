"""Stream manager for handling video transcoding and playback setup."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Any

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import build_ffmpeg_cmd
from pikaraoke.lib.file_resolver import FileResolver, is_transcoding_required
from pikaraoke.lib.preference_manager import PreferenceManager


@dataclass
class PlaybackResult:
    """Result of a playback operation.

    Attributes:
        success: Whether playback started successfully.
        stream_url: URL path for the video stream.
        subtitle_url: URL path for subtitles (if present).
        duration: Video duration in seconds.
        error: Error message if playback failed.
    """

    success: bool
    stream_url: str | None = None
    subtitle_url: str | None = None
    duration: int | None = None
    error: str | None = None


def enqueue_output(out: Any, queue: Queue) -> None:
    """Read lines from a stream and put them in a queue without blocking.

    Args:
        out: File-like object to read from (e.g., subprocess stderr).
        queue: Queue to put the read lines into.
    """
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


class StreamManager:
    """Manages video transcoding and stream preparation for playback.

    Handles FFmpeg transcoding, buffering monitoring, and stream URL setup
    for both HLS and progressive MP4 streaming formats.

    Attributes:
        preferences: PreferenceManager for configuration.
        ffmpeg_process: Currently running FFmpeg subprocess.
        ffmpeg_log: Queue for FFmpeg stderr output.
    """

    def __init__(self, preferences: PreferenceManager, streaming_format: str = "hls") -> None:
        """Initialize the stream manager.

        Args:
            preferences: PreferenceManager instance for configuration.
            streaming_format: Video streaming format ('hls' or 'mp4').
        """
        self.preferences = preferences
        self.streaming_format = streaming_format
        self.ffmpeg_process = None
        self.ffmpeg_log: Queue | None = None

    def play_file(self, file_path: str, semitones: int = 0) -> PlaybackResult:
        """Start playback of a media file.

        Handles file resolution, transcoding, and stream setup.

        Args:
            file_path: Path to the media file to play.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            PlaybackResult with success status and stream information.
        """
        from flask_babel import _

        streaming_format = self.streaming_format
        normalize_audio = self.preferences.get_or_default("normalize_audio")
        avsync = self.preferences.get_or_default("avsync")
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )

        is_hls = streaming_format == "hls"

        requires_transcoding = (
            semitones != 0
            or normalize_audio
            or is_transcoding_required(file_path)
            or avsync != 0
            or is_hls
        )

        logging.debug(f"Requires transcoding: {requires_transcoding}")

        try:
            fr = FileResolver(file_path, streaming_format)
        except Exception as e:
            error_message = _("Error resolving file: %s") % str(e)
            logging.error(error_message)
            return PlaybackResult(success=False, error=error_message)

        # Set stream URL based on format
        if is_hls:
            stream_url_path = f"/stream/{fr.stream_uid}.m3u8"
        else:
            if complete_transcode_before_play or not requires_transcoding:
                stream_url_path = f"/stream/full/{fr.stream_uid}"
            else:
                stream_url_path = f"/stream/{fr.stream_uid}.mp4"

        if not requires_transcoding:
            is_transcoding_complete = self._copy_file(file_path, fr.output_file)
            is_buffering_complete = True
        else:
            is_transcoding_complete, is_buffering_complete = self._transcode_file(
                fr, semitones, is_hls
            )

        subtitle_url = None
        if fr.ass_file_path:
            subtitle_url = f"/subtitle/{fr.stream_uid}"
            logging.debug(f"Subtitle file found: {fr.ass_file_path}. URL: {subtitle_url}")

        # Check if the stream is ready to play
        if is_transcoding_complete or is_buffering_complete:
            logging.debug("Stream ready!")
            return PlaybackResult(
                success=True,
                stream_url=stream_url_path,
                subtitle_url=subtitle_url,
                duration=fr.duration,
            )
        else:
            error_message = _("Failed to prepare stream")
            logging.error(error_message)
            return PlaybackResult(success=False, error=error_message)

    def _copy_file(self, src_path: str, dest_path: str) -> bool:
        """Copy a file that doesn't need transcoding.

        Args:
            src_path: Source file path.
            dest_path: Destination file path.

        Returns:
            True if copy succeeded, False otherwise.
        """
        shutil.copy(src_path, dest_path)
        max_retries = 5
        while max_retries > 0:
            if os.path.exists(dest_path):
                return True
            max_retries -= 1
            time.sleep(1)
        logging.debug(f"Copying file failed: {dest_path}")
        return False

    def _transcode_file(self, fr: FileResolver, semitones: int, is_hls: bool) -> tuple[bool, bool]:
        """Transcode a file using FFmpeg.

        Args:
            fr: FileResolver instance with file information.
            semitones: Semitones to transpose.
            is_hls: Whether to use HLS streaming format.

        Returns:
            Tuple of (is_transcoding_complete, is_buffering_complete).
        """
        self.kill_ffmpeg()

        normalize_audio = self.preferences.get_or_default("normalize_audio")
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )
        avsync = self.preferences.get_or_default("avsync")
        cdg_pixel_scaling = self.preferences.get_or_default("cdg_pixel_scaling")
        buffer_size = int(self.preferences.get_or_default("buffer_size")) * 1000

        ffmpeg_cmd = build_ffmpeg_cmd(
            fr,
            semitones,
            normalize_audio,
            not is_hls,  # force mp4 encoding
            complete_transcode_before_play,
            avsync,
            cdg_pixel_scaling,
        )
        self.ffmpeg_process = ffmpeg_cmd.run_async(pipe_stderr=True, pipe_stdin=True)

        # FFmpeg outputs to stderr - prevent blocking reads
        self.ffmpeg_log = Queue()
        t = Thread(
            target=enqueue_output,
            args=(self.ffmpeg_process.stderr, self.ffmpeg_log),
            daemon=True,
        )
        t.start()

        transcode_max_retries = 2500  # ~2 minutes max
        is_transcoding_complete = False
        is_buffering_complete = False

        # Transcoding readiness polling loop
        while True:
            self.log_ffmpeg_output()

            # Check if FFmpeg has exited
            if self.ffmpeg_process.poll() is not None:
                exitcode = self.ffmpeg_process.poll()
                if exitcode != 0:
                    logging.error(f"FFmpeg exited with code {exitcode}")
                    break
                else:
                    is_transcoding_complete = True
                    stream_size = fr.get_current_stream_size()
                    logging.debug(f"Transcoding complete. Output size: {stream_size}")
                    break

            # Check buffering progress based on streaming format
            if is_hls:
                is_buffering_complete = self._check_hls_buffer(fr, buffer_size)
            else:
                is_buffering_complete = self._check_mp4_buffer(fr, buffer_size)

            if is_buffering_complete:
                break

            # Prevent infinite loop
            if transcode_max_retries <= 0:
                logging.error("Max retries reached trying to play song")
                break
            transcode_max_retries -= 1
            time.sleep(0.05)

        return is_transcoding_complete, is_buffering_complete

    def _check_hls_buffer(self, fr: FileResolver, buffer_size: int) -> bool:
        """Check if HLS buffer is ready for playback.

        Counts segment files directly instead of parsing the playlist.
        This works with hls_playlist_type=vod (playlist written at end)
        while still allowing early playback detection.

        Args:
            fr: FileResolver instance.
            buffer_size: Minimum buffer size in bytes.

        Returns:
            True if buffer is ready, False otherwise.
        """
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )
        if complete_transcode_before_play:
            return False

        try:
            # Check if the playlist exists and has content
            if not os.path.exists(fr.output_file):
                return False
            if os.path.getsize(fr.output_file) == 0:
                return False

            # Count segment files directly (works even before playlist is written)
            stream_uid_str = str(fr.stream_uid)
            segment_files = [
                f for f in os.listdir(fr.tmp_dir) if stream_uid_str in f and f.endswith(".m4s")
            ]
            segment_count = len(segment_files)
            min_segments = 3

            if segment_count >= min_segments:
                stream_size = fr.get_current_stream_size()
                if stream_size >= buffer_size:
                    logging.debug(
                        f"Buffering complete. Stream size: {stream_size}, "
                        f"Segments: {segment_count}"
                    )
                    return True
        except FileNotFoundError:
            pass  # Temp dir doesn't exist yet
        except OSError as e:
            logging.warning(f"I/O error checking buffer: {e}")
        except Exception as e:
            logging.error(f"Unexpected error during buffering check: {e}")

        return False

    def _check_mp4_buffer(self, fr: FileResolver, buffer_size: int) -> bool:
        """Check if MP4 buffer is ready for playback.

        Args:
            fr: FileResolver instance.
            buffer_size: Minimum buffer size in bytes.

        Returns:
            True if buffer is ready, False otherwise.
        """
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )
        if complete_transcode_before_play:
            return False

        try:
            output_file_size = os.path.getsize(fr.output_file)
            if output_file_size > buffer_size:
                logging.debug(f"Buffering complete. File size: {output_file_size}")
                return True
        except FileNotFoundError:
            pass

        return False

    def log_ffmpeg_output(self) -> None:
        """Log any pending FFmpeg output from the queue."""
        if self.ffmpeg_log is None:
            return
        while self.ffmpeg_log.qsize() > 0:
            output = self.ffmpeg_log.get_nowait()
            logging.debug("[FFMPEG] " + output.decode("utf-8", "ignore").strip())

    def kill_ffmpeg(self) -> None:
        """Terminate the running FFmpeg process gracefully.

        Uses SIGTERM first, then SIGKILL if needed.
        Critical for Raspberry Pi to release GPU memory from h264_v4l2m2m encoder.
        """
        if self.ffmpeg_process:
            logging.debug("Terminating ffmpeg process gracefully")
            try:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=5)
                logging.debug("FFmpeg process terminated gracefully")
            except subprocess.TimeoutExpired:
                logging.warning("FFmpeg did not terminate gracefully, forcing kill")
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait()
                logging.debug("FFmpeg process force killed")
            except Exception as e:
                logging.debug(f"FFmpeg termination exception: {e}")
            finally:
                self.ffmpeg_process = None
