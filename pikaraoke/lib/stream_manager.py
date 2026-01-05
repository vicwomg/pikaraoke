"""Stream manager for handling video transcoding and playback setup."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from queue import Queue
from threading import Thread
from typing import TYPE_CHECKING, Any

from pikaraoke.lib.ffmpeg import build_ffmpeg_cmd
from pikaraoke.lib.file_resolver import FileResolver, is_transcoding_required

if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


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
        karaoke: Reference to the Karaoke instance.
        ffmpeg_process: Currently running FFmpeg subprocess.
        ffmpeg_log: Queue for FFmpeg stderr output.
    """

    def __init__(self, karaoke: Karaoke) -> None:
        """Initialize the stream manager.

        Args:
            karaoke: Reference to the Karaoke instance for config and callbacks.
        """
        self.karaoke = karaoke
        self.ffmpeg_process = None
        self.ffmpeg_log: Queue | None = None

    def play_file(self, file_path: str, semitones: int = 0) -> bool | None:
        """Start playback of a media file.

        Handles file resolution, transcoding, and stream setup.

        Args:
            file_path: Path to the media file to play.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            False if file resolution fails, None otherwise.
        """
        from flask_babel import _

        k = self.karaoke
        logging.info(f"Playing file: {file_path} transposed {semitones} semitones")

        is_hls = k.streaming_format == "hls"

        requires_transcoding = (
            semitones != 0
            or k.normalize_audio
            or is_transcoding_required(file_path)
            or k.avsync != 0
            or is_hls
        )

        logging.debug(f"Requires transcoding: {requires_transcoding}")

        try:
            fr = FileResolver(file_path, k.streaming_format)
        except Exception as e:
            error_message = _("Error resolving file: %s") % str(e)
            k.queue.pop(0)
            k.end_song(reason=error_message)
            k.log_and_send(error_message, "danger")
            return False

        # Set stream URL based on format
        if is_hls:
            stream_url_path = f"/stream/{fr.stream_uid}.m3u8"
        else:
            if k.complete_transcode_before_play or not requires_transcoding:
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
            self._setup_now_playing(k, file_path, fr, semitones, stream_url_path, subtitle_url)

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
        k = self.karaoke
        self.kill_ffmpeg()

        ffmpeg_cmd = build_ffmpeg_cmd(
            fr,
            semitones,
            k.normalize_audio,
            not is_hls,  # force mp4 encoding
            k.complete_transcode_before_play,
            k.avsync,
            k.cdg_pixel_scaling,
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
        buffer_size = int(k.buffer_size) * 1000

        # Transcoding readiness polling loop
        while True:
            self.log_ffmpeg_output()

            # Check if FFmpeg has exited
            if self.ffmpeg_process.poll() is not None:
                exitcode = self.ffmpeg_process.poll()
                if exitcode != 0:
                    logging.error(f"FFmpeg exited with code {exitcode}. Skipping track")
                    k.end_song()
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
                logging.error("Max retries reached trying to play song. Skipping track")
                k.end_song()
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
        k = self.karaoke
        if k.complete_transcode_before_play:
            return False

        try:
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
        except (PermissionError, OSError, IOError) as e:
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
        k = self.karaoke
        if k.complete_transcode_before_play:
            return False

        try:
            output_file_size = os.path.getsize(fr.output_file)
            if output_file_size > buffer_size:
                logging.debug(f"Buffering complete. File size: {output_file_size}")
                return True
        except FileNotFoundError:
            pass

        return False

    def _setup_now_playing(
        self,
        k: Karaoke,
        file_path: str,
        fr: FileResolver,
        semitones: int,
        stream_url_path: str,
        subtitle_url: str | None,
    ) -> None:
        """Set up the now playing state and wait for playback to start.

        Args:
            k: Karaoke instance.
            file_path: Path to the media file.
            fr: FileResolver instance.
            semitones: Transpose value.
            stream_url_path: URL path for the stream.
            subtitle_url: URL path for the subtitle file.
        """
        logging.debug("Stream ready!")
        k.now_playing = k.filename_from_path(file_path)
        k.now_playing_filename = file_path
        k.now_playing_transpose = semitones
        k.now_playing_duration = fr.duration
        k.now_playing_url = stream_url_path
        k.now_playing_subtitle_url = subtitle_url
        k.now_playing_user = k.queue[0]["user"]
        k.is_paused = False
        k.queue.pop(0)
        k.update_now_playing_socket()
        k.update_queue_socket()

        # Wait for stream to start playing
        max_retries = 100
        while not k.is_playing and max_retries > 0:
            time.sleep(0.1)
            max_retries -= 1

        if k.is_playing:
            logging.debug("Stream is playing")
        else:
            logging.error("Stream was not playable! Skipping track")
            k.end_song()

    def log_ffmpeg_output(self) -> None:
        """Log any pending FFmpeg output from the queue."""
        if self.ffmpeg_log is not None and self.ffmpeg_log.qsize() > 0:
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
