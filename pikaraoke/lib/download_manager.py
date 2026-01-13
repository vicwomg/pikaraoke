"""Download queue manager for serialized video downloads."""

from __future__ import annotations

import logging
import re
import subprocess
from queue import Queue
from threading import Thread
from typing import TYPE_CHECKING

from pikaraoke.lib.youtube_dl import build_ytdl_download_command


def parse_download_path(output: str) -> str | None:
    """Parse the downloaded file path from yt-dlp output.

    Args:
        output: Combined stdout/stderr from yt-dlp.

    Returns:
        Path to the downloaded file, or None if not found.
    """

    # Pattern 1: [Merger] Merging formats into "/path/to/file.ext"
    match = re.search(r'\[Merger\] Merging formats into "(.+)"', output)
    if match:
        return match.group(1).strip()

    # Pattern 2: [download] Destination: /path/to/file.ext
    match = re.search(r"\[download\] Destination: (.+)$", output, re.MULTILINE)
    if match:
        return match.group(1).strip()

    # Pattern 3: [download] /path/to/file.ext has already been downloaded
    match = re.search(r"\[download\] (.+) has already been downloaded", output)
    if match:
        return match.group(1).strip()

    return None


if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


class DownloadManager:
    """Manages a queue of video downloads, processing them serially.

    This prevents rate limiting from download sources and reduces CPU load
    by ensuring only one download runs at a time.

    Attributes:
        download_queue: Queue holding pending download requests.
    """

    def __init__(self, karaoke: Karaoke) -> None:
        """Initialize the download manager.

        Args:
            karaoke: Reference to the Karaoke instance for config and callbacks.
        """
        self.karaoke = karaoke
        self.download_queue: Queue = Queue()
        self.pending_downloads: list[dict] = []  # Shadow queue for visibility
        self.active_download: dict | None = None
        self._worker_thread: Thread | None = None
        self._is_downloading: bool = False  # Track if a download is currently in progress

    def start(self) -> None:
        """Start the download worker thread."""
        self._worker_thread = Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        logging.debug("Download queue worker started")

    def get_downloads_status(self) -> dict:
        """Get the status of active and pending downloads.

        Returns:
            Dict containing 'active' download info and list of 'pending' downloads.
        """
        return {"active": self.active_download, "pending": self.pending_downloads}

    def queue_download(
        self,
        video_url: str,
        enqueue: bool = False,
        user: str = "Pikaraoke",
        title: str | None = None,
    ) -> None:
        """Queue a video for download.

        Downloads are processed serially to prevent rate limiting and CPU overload.
        A notification is sent when the download is queued, and another when it starts.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to playback queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).
        """
        from flask_babel import _

        displayed_title = title if title else video_url

        # Check how many items are ahead (in queue + currently downloading)
        pending_count = self.download_queue.qsize() + (1 if self._is_downloading else 0)

        if pending_count > 0:
            # MSG: Message shown when download is added to queue (not first in line)
            self.karaoke.log_and_send(
                _("Download queued (#%d): %s") % (pending_count + 1, displayed_title)
            )
        else:
            # MSG: Message shown when download is added and will start immediately
            self.karaoke.log_and_send(_("Download starting: %s") % displayed_title)

        download_data = {
            "video_url": video_url,
            "enqueue": enqueue,
            "user": user,
            "title": title,
            "display_title": displayed_title,
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
            self.active_download = {
                "title": download_request.get("display_title", download_request["video_url"]),
                "url": download_request["video_url"],
                "user": download_request["user"],
                "progress": 0.0,
                "status": "starting",
                "eta": "--:--",
                "speed": "---",
            }

            try:
                self._execute_download(
                    download_request["video_url"],
                    download_request["enqueue"],
                    download_request["user"],
                    download_request["title"],
                )
            except Exception as e:
                logging.error(f"Error processing download: {e}")
            finally:
                self._is_downloading = False
                self.active_download = None
                self.download_queue.task_done()

    def _execute_download(
        self,
        video_url: str,
        enqueue: bool,
        user: str,
        title: str | None,
    ) -> int:
        """Execute a video download.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).

        Returns:
            Return code from the download process (0 = success).
        """
        from flask_babel import _

        k = self.karaoke
        displayed_title = title if title else video_url

        # MSG: Message shown when download actually starts (after waiting in queue)
        k.log_and_send(_("Downloading video: %s") % displayed_title)

        cmd = build_ytdl_download_command(
            k.youtubedl_path,
            video_url,
            k.download_path,
            k.high_quality,
            k.youtubedl_proxy,
            k.additional_ytdl_args,
        )
        logging.debug("Youtube-dl command: " + " ".join(cmd))

        # Use Popen to capture output in real-time
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            universal_newlines=True,
        )

        output_buffer = []

        # Regex to parse progress from yt-dlp stdout
        # Example: [download]   0.0% of    4.62MiB at  396.66KiB/s ETA 00:12
        progress_regex = re.compile(
            r"\[download\]\s+(\d+\.?\d*)%\s+of\s+[^\s]+\s+at\s+([^\s]+)\s+ETA\s+([^\s]+)"
        )

        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                output_buffer.append(line)
                match = progress_regex.search(line)
                if match and self.active_download:
                    percent = float(match.group(1))
                    speed = match.group(2)
                    eta = match.group(3)

                    self.active_download["progress"] = percent
                    self.active_download["status"] = "downloading"
                    self.active_download["speed"] = speed
                    self.active_download["eta"] = eta
                # Log only non-progress lines to avoid spamming logs, or log everything at debug
                # logging.debug(line.strip())

        rc = process.poll()
        output = "".join(output_buffer)

        if rc != 0:
            logging.error("Error code while downloading, retrying once...")
            # Simple retry logic (synchronous for now, or could re-queue)
            # For simplicity, we just run it again with run() since we failed
            # But normally we should loop Popen again.
            # Sticking to original logic's retry pattern but using basic run for retry to keep it simple
            # or we could make _execute_download support retries internally.
            # Let's just do a subprocess.run for the retry to save complexity,
            # although we lose progress bars for the retry.
            result = subprocess.run(cmd, capture_output=True, text=True)
            rc = result.returncode
            output = result.stdout + result.stderr

        if rc == 0:
            if self.active_download:
                self.active_download["progress"] = 100
                self.active_download["status"] = "complete"

            if enqueue:
                # MSG: Message shown after the download is completed and queued
                k.log_and_send(_("Downloaded and queued: %s") % displayed_title, "success")
            else:
                # MSG: Message shown after the download is completed but not queued
                k.log_and_send(_("Downloaded: %s") % displayed_title, "success")

            # Extract the downloaded file path from yt-dlp output
            song_path = parse_download_path(output)
            logging.debug(output)

            if song_path:
                k.available_songs.add_if_valid(song_path)
            else:
                logging.warning("Could not parse download path from yt-dlp output")

            if enqueue:
                if song_path:
                    k.enqueue(song_path, user, log_action=False)
                else:
                    # MSG: Message shown after the download is completed but the adding to queue fails
                    k.log_and_send(_("Error queueing song: ") + displayed_title, "danger")
        else:
            # MSG: Message shown after the download process is completed but the song is not found
            k.log_and_send(_("Error downloading song: ") + displayed_title, "danger")
            logging.error(f"yt-dlp stderr: {output}")

        return rc
