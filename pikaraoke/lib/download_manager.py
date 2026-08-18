"""Download queue manager for serialized video downloads."""

from __future__ import annotations

import logging
import re
import subprocess
import uuid
from queue import Queue

import psutil
from gevent import Greenlet, spawn

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.get_platform import is_windows
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_manager import SongManager
from pikaraoke.lib.youtube_dl import (
    build_ytdl_download_command,
    get_youtube_id_from_url,
)

# YouTube refuses a freshly resolved URL with a 403 on roughly half of all downloads,
# regardless of the format selected, and re-extracting from scratch is the only known
# remedy. At that rate three attempts would still leave one download in eight failing.
MAX_DOWNLOAD_ATTEMPTS = 5


def _use_spare_capacity(process: subprocess.Popen) -> None:
    """Drop a download to background priority so it never competes with playback.

    Priority is inherited, which is what covers the ffmpeg merge yt-dlp spawns.
    """
    try:
        child = psutil.Process(process.pid)
        if is_windows():
            child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            child.ionice(psutil.IOPRIO_VERYLOW)
        else:
            child.nice(10)
            # macOS has no ionice at all, hence AttributeError below.
            child.ionice(psutil.IOPRIO_CLASS_IDLE)
    except (psutil.Error, AttributeError, NotImplementedError, OSError) as e:
        logging.debug(f"Could not lower download priority: {e}")


class DownloadManager:
    """Manages a queue of video downloads, processing them serially.

    This prevents rate limiting from download sources and reduces CPU load
    by ensuring only one download runs at a time.

    Attributes:
        download_queue: Queue holding pending download requests.
    """

    def __init__(
        self,
        events: EventSystem,
        preferences: PreferenceManager,
        song_manager: SongManager,
        queue_manager: QueueManager,
        download_path: str,
        youtubedl_proxy: str | None = None,
        additional_ytdl_args: str | None = None,
    ) -> None:
        """Initialize the download manager.

        Args:
            events: Event system for notifications and broadcasts.
            preferences: Configuration manager for persistent settings.
            song_manager: Manager for song library operations.
            queue_manager: Manager for playback queue.
            download_path: Directory where downloads are saved.
            youtubedl_proxy: Optional proxy URL for yt-dlp.
            additional_ytdl_args: Optional additional arguments for yt-dlp.
        """
        self._events = events
        self._preferences = preferences
        self._song_manager = song_manager
        self._queue_manager = queue_manager
        self._download_path = download_path
        self._youtubedl_proxy = youtubedl_proxy
        self._additional_ytdl_args = additional_ytdl_args
        self.download_queue: Queue = Queue()
        self.pending_downloads: list[dict] = []  # Shadow queue for visibility
        self.download_errors: list[dict] = []  # Track failed downloads
        self.active_download: dict | None = None
        self._worker: Greenlet | None = None
        self._is_downloading: bool = False  # Track if a download is currently in progress

    def start(self) -> None:
        """Start the download worker greenlet.

        Runs as a greenlet on the main gevent hub rather than a raw OS thread:
        a separate thread touching monkey-patched primitives (Queue, subprocess)
        spins up a second hub, which races the main hub at WSGIServer startup and
        can deadlock (observed hanging PiKaraoke launch on macOS).
        """
        self._worker = spawn(self._process_queue)
        logging.debug("Download queue worker started")

    def get_downloads_status(self) -> dict:
        """Get the status of active and pending downloads.

        Returns:
            Dict containing 'active' download info and list of 'pending' downloads.
        """
        return {
            "active": self.active_download,
            "pending": self.pending_downloads,
            "errors": self.download_errors,
            "max_attempts": MAX_DOWNLOAD_ATTEMPTS,
        }

    def remove_error(self, error_id: str) -> bool:
        """Remove an error from the list by ID.

        Args:
            error_id: The ID of the error to remove.

        Returns:
            True if removed, False if not found.
        """
        initial_len = len(self.download_errors)
        self.download_errors = [e for e in self.download_errors if e["id"] != error_id]
        return len(self.download_errors) < initial_len

    def retry_error(self, error_id: str) -> bool:
        """Re-queue a failed download and drop its error entry.

        Args:
            error_id: The ID of the error to retry.

        Returns:
            True if re-queued, False if the error was not found.
        """
        entry = next((e for e in self.download_errors if e["id"] == error_id), None)
        if entry is None:
            return False

        self.remove_error(error_id)
        self.queue_download(
            entry["url"],
            enqueue=entry["enqueue"],
            user=entry["user"],
            title=entry["title"],
        )
        return True

    def queue_download(
        self,
        video_url: str,
        enqueue: bool = False,
        user: str = "Pikaraoke",
        title: str | None = None,
    ) -> None:
        """Queue a video for download.

        Downloads are processed serially to prevent rate limiting and CPU overload.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to playback queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).
        """
        from flask_babel import _

        # Strip playlist parameter to avoid downloading entire playlists
        if "&list=" in video_url:
            video_url = video_url.split("&list=")[0]

        displayed_title = title if title else video_url

        # Check how many items are ahead (in queue + currently downloading)
        pending_count = self.download_queue.qsize() + (1 if self._is_downloading else 0)

        if pending_count > 0:
            # MSG: Message shown when download is added to queue (not first in line)
            self._events.emit(
                "notification",
                _("Download queued (#%d): %s") % (pending_count + 1, displayed_title),
            )
        else:
            # MSG: Message shown when download is added and will start immediately
            self._events.emit("notification", _("Download starting: %s") % displayed_title)

        # If queue was just started (was not downloading before), emit event
        if not self._is_downloading and self.download_queue.empty():
            self._events.emit("download_started")

        download_data = {
            "video_url": video_url,
            "enqueue": enqueue,
            "user": user,
            "title": title,
            "display_title": displayed_title,
            "attempts": 0,
        }

        # Add to the download queue and shadow list
        self.download_queue.put(download_data)
        self.pending_downloads.append(download_data)

    def _process_queue(self) -> None:
        """Worker thread that processes downloads from the queue serially.

        Runs indefinitely, blocking on queue.get() until items are available.
        Each download is processed completely before the next one starts.
        """
        while True:
            download_request = self.download_queue.get()

            # Remove from shadow queue
            # Note: Since this is a single worker thread and append happens on main thread,
            # we simply pop the first item as it corresponds to FIFO queue.
            # In a multi-worker scenario, this would need a lock.
            if self.pending_downloads:
                self.pending_downloads.pop(0)

            self._is_downloading = True

            # Initialize active download state
            attempts = download_request["attempts"]
            self.active_download = {
                "title": download_request.get("display_title", download_request["video_url"]),
                "url": download_request["video_url"],
                "user": download_request["user"],
                "progress": 0.0,
                "status": "retrying" if attempts else "starting",
                "attempts": attempts,
                "eta": "--:--",
                "speed": "---",
            }

            try:
                self._execute_download(download_request)
            except Exception as e:
                logging.error(f"Error processing download: {e}")
            finally:
                self._is_downloading = False
                self.active_download = None
                self.download_queue.task_done()

                # Check if we are done with all downloads
                if self.download_queue.empty():
                    self._events.emit("download_stopped")

    def _run_ytdl(self, cmd: list[str]) -> tuple[int, str]:
        """Run yt-dlp to completion, streaming its progress into active_download.

        Returns:
            Tuple of (return code, captured output).
        """
        # Use Popen to capture output in real-time
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            universal_newlines=True,
        )
        _use_spare_capacity(process)

        output_buffer = []

        # Regex to parse progress from yt-dlp stdout
        # Example: [download]   0.0% of    4.62MiB at  396.66KiB/s ETA 00:12
        progress_regex = re.compile(
            r"\[download\]\s+(\d+\.?\d*)%\s+of\s+.*?\s+at\s+([^\s]+)\s+ETA\s+([^\s]+)"
        )

        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                output_buffer.append(line)
                match = progress_regex.search(line)
                if match and self.active_download:
                    self.active_download["progress"] = float(match.group(1))
                    self.active_download["status"] = "downloading"
                    self.active_download["speed"] = match.group(2)
                    self.active_download["eta"] = match.group(3)

        rc = process.poll()
        output = "".join(output_buffer)
        if rc == 0:
            # A retry that succeeds is only diagnosable against the attempt that failed,
            # so the winning attempt's output has to be kept as well.
            logging.debug(f"yt-dlp output: {output}")
        return rc, output

    def _requeue(self, request: dict) -> bool:
        """Put a failed download back at the end of the queue.

        Back-of-queue placement is the backoff: any download waiting behind the failed
        one overtakes it, so a dead link can never block the serialized worker.

        Returns:
            True if re-queued, False once the attempt cap is reached.
        """
        attempts = request["attempts"] + 1
        if attempts >= MAX_DOWNLOAD_ATTEMPTS:
            return False

        request["attempts"] = attempts
        if self.active_download:
            self.active_download["progress"] = 0.0
            self.active_download["status"] = "retrying"
            self.active_download["attempts"] = attempts

        self.download_queue.put(request)
        self.pending_downloads.append(request)
        logging.info(
            f"Re-queueing failed download {request['video_url']} "
            f"(attempt {attempts + 1} of {MAX_DOWNLOAD_ATTEMPTS})"
        )
        return True

    def _execute_download(self, request: dict) -> int:
        """Execute a video download, re-queueing it if it fails with attempts to spare.

        Args:
            request: Download request as built by queue_download.

        Returns:
            Return code from the download process (0 = success).
        """
        from flask_babel import _

        video_url = request["video_url"]
        enqueue = request["enqueue"]
        user = request["user"]
        displayed_title = request["title"] if request["title"] else video_url

        # A retry is a quiet safety net, and the queue page already shows it. Repeating
        # this toast on every attempt just reads as a glitch.
        if not request["attempts"]:
            # MSG: Message shown when download actually starts (after waiting in queue)
            self._events.emit("notification", _("Downloading video: %s") % displayed_title)

        cmd = build_ytdl_download_command(
            video_url,
            self._download_path,
            self._preferences.get_or_default("high_quality"),
            self._youtubedl_proxy,
            self._additional_ytdl_args,
        )
        logging.debug("yt-dlp command: " + " ".join(cmd))

        try:
            rc, output = self._run_ytdl(cmd)
        except Exception as e:
            # Deliberately broad: whatever goes wrong spawning or reading yt-dlp, the request
            # has to reach the retry and error card below rather than vanish out of the worker.
            logging.error(f"yt-dlp invocation failed for {video_url}: {e}")
            rc, output = 1, f"{type(e).__name__}: {e}"

        if rc != 0:
            logging.error(f"yt-dlp stderr: {output}")
            if self._requeue(request):
                return rc

            # MSG: Message shown after the download process is completed but the song is not found
            self._events.emit(
                "notification", _("Error downloading song: ") + displayed_title, "danger"
            )
            self.download_errors.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": displayed_title,
                    "url": video_url,
                    "user": user,
                    "enqueue": enqueue,
                    "error": output or "Unknown error",
                }
            )
        else:
            if self.active_download:
                self.active_download["progress"] = 100
                self.active_download["status"] = "complete"

            if enqueue:
                # MSG: Message shown after the download is completed and queued
                self._events.emit(
                    "notification", _("Downloaded and queued: %s") % displayed_title, "success"
                )
            else:
                # MSG: Message shown after the download is completed but not queued
                self._events.emit("notification", _("Downloaded: %s") % displayed_title, "success")

            # After download, find the file path by ID
            song_path = None
            video_id = get_youtube_id_from_url(video_url)
            if video_id:
                logging.debug(f"Searching for downloaded file by ID: {video_id}")
                song_path = self._song_manager.songs.find_by_id(self._download_path, video_id)
            else:
                logging.warning("No video ID available to find downloaded song")

            if song_path:
                self._events.emit("song_downloaded", song_path, video_id)
            else:
                logging.warning(
                    f"Could not find downloaded song in {self._download_path} matching ID: {video_id}"
                )

            if enqueue:
                if song_path:
                    self._queue_manager.enqueue(song_path, user, log_action=False)
                else:
                    # MSG: Message shown after the download is completed but the adding to queue fails
                    self._events.emit(
                        "notification", _("Error queueing song: ") + displayed_title, "danger"
                    )

        return rc
