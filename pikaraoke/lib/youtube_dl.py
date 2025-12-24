"""YouTube download utilities using yt-dlp."""

from __future__ import annotations

import logging
import queue
import shlex
import subprocess
import threading
from typing import Callable

from flask_babel import _

from pikaraoke.lib.get_platform import get_installed_js_runtime
from pikaraoke.lib.on_screen_notification import OnScreenNotification


class YtDlpClient:
    """Client for downloading videos from YouTube using yt-dlp.

    Manages a background download queue to process downloads sequentially.

    Attributes:
        youtubedl_path: Path to the yt-dlp executable.
        youtubedl_proxy: Optional proxy server URL.
        additional_args: Additional command-line arguments for yt-dlp.
        notification: OnScreenNotification instance for displaying messages.
        download_queue: Queue of pending download tasks.
        is_downloading: Whether a download is currently in progress.
    """

    def __init__(
        self,
        youtubedl_path: str,
        notification_instance: OnScreenNotification,
        youtubedl_proxy: str | None = None,
        additional_args: str | None = None,
    ) -> None:
        """Initialize the YtDlpClient.

        Args:
            youtubedl_path: Path to the yt-dlp executable.
            notification_instance: OnScreenNotification instance for displaying messages.
            youtubedl_proxy: Optional proxy server URL.
            additional_args: Additional command-line arguments for yt-dlp.
        """
        self.youtubedl_path = youtubedl_path
        self.youtubedl_proxy = youtubedl_proxy
        self.additional_args = additional_args
        self.notification = notification_instance
        self.download_queue: queue.Queue = queue.Queue()
        self.is_downloading: bool = False
        self.queue_worker_thread: threading.Thread | None = None

        self._start_queue_worker()

    def get_version(self) -> str:
        """Get the installed yt-dlp version.

        Returns:
            Version string of the installed yt-dlp.
        """
        return subprocess.check_output([self.youtubedl_path, "--version"]).strip().decode("utf8")

    @staticmethod
    def get_youtube_id_from_url(url: str) -> str | None:
        """Extract the YouTube video ID from a URL.

        Supports youtube.com/watch?v=, m.youtube.com/?v=, and youtu.be/ formats.

        Args:
            url: YouTube video URL.

        Returns:
            The video ID string, or None if parsing failed.
        """
        if "v=" in url:  # accommodates youtube.com/watch?v= and m.youtube.com/?v=
            s = url.split("watch?v=")
        else:  # accommodates youtu.be/
            s = url.split("u.be/")
        if len(s) == 2:
            if "?" in s[1]:  # Strip unneeded YouTube params
                s[1] = s[1][0 : s[1].index("?")]
            return s[1]
        else:
            logging.error("Error parsing youtube id from url: " + url)
            return None

    def upgrade(self) -> str:
        """Upgrade yt-dlp to the latest version.

        Attempts self-upgrade first, then falls back to pip if needed.

        Returns:
            The new version string after upgrade.
        """
        try:
            output = (
                subprocess.check_output([self.youtubedl_path, "-U"], stderr=subprocess.STDOUT)
                .decode("utf8")
                .strip()
            )
        except subprocess.CalledProcessError as e:
            output = e.output.decode("utf8")
        logging.info(output)
        if "You installed yt-dlp with pip or using the wheel from PyPi" in output:
            # allow pip to break system packages (probably required if installed without venv)
            args = ["install", "--upgrade", "yt-dlp[default]", "--break-system-packages"]
            try:
                logging.info("Attempting youtube-dl upgrade via pip3...")
                output = (
                    subprocess.check_output(["pip3"] + args, stderr=subprocess.STDOUT)
                    .decode("utf8")
                    .strip()
                )
            except FileNotFoundError:
                logging.info("Attempting youtube-dl upgrade via pip...")
                output = (
                    subprocess.check_output(["pip"] + args, stderr=subprocess.STDOUT)
                    .decode("utf8")
                    .strip()
                )
        return self.get_version()

    def build_download_command(
        self, video_url: str, download_path: str, high_quality: bool = False
    ) -> list[str]:
        """Build the yt-dlp command line for downloading a video.

        Args:
            video_url: URL of the video to download.
            download_path: Directory path where videos will be saved.
            high_quality: If True, download up to 1080p; otherwise download mp4.

        Returns:
            List of command-line arguments for subprocess execution.
        """
        dl_path = download_path + "%(title)s---%(id)s.%(ext)s"
        file_quality = (
            "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
            if high_quality
            else "mp4"
        )
        cmd = [
            self.youtubedl_path,
            "-f",
            file_quality,
            "-o",
            dl_path,
            "-S",
            "vcodec:h264",
        ]

        preferred_js_runtime = get_installed_js_runtime()
        if preferred_js_runtime and preferred_js_runtime != "deno":
            # Deno is automatically assumed by yt-dlp, and does not need specification here
            cmd += ["--js-runtimes", preferred_js_runtime]

        proxy = self.youtubedl_proxy
        if proxy:
            cmd += ["--proxy", proxy]

        extra_args = self.additional_args
        if extra_args:
            cmd += shlex.split(extra_args)

        cmd += [video_url]
        return cmd

    def _start_queue_worker(self) -> None:
        """Start the background worker thread that processes downloads from the queue."""
        self.queue_worker_thread = threading.Thread(
            target=self._process_download_queue, daemon=True
        )
        self.queue_worker_thread.start()

    def _process_download_queue(self) -> None:
        """Worker thread that processes downloads sequentially from the queue."""
        while True:
            try:
                # This blocks until an item is available
                download_task = self.download_queue.get(timeout=None)

                if download_task is None:  # Sentinel value to stop the worker
                    break

                video_url, download_path, high_quality, title, on_complete = download_task
                self.is_downloading = True

                try:
                    rc = self._download_video(
                        video_url=video_url,
                        download_path=download_path,
                        high_quality=high_quality,
                        title=title,
                    )

                    if on_complete:
                        on_complete(rc == 0, video_url, title)

                finally:
                    self.is_downloading = False

                self.download_queue.task_done()

            except queue.Empty:
                # This shouldn't happen with timeout=None, but good practice
                continue
            except Exception as e:
                logging.error(f"Error processing download from queue: {e}")
                self.is_downloading = False
                try:
                    self.download_queue.task_done()
                except ValueError:
                    pass

    def _download_video(
        self,
        video_url: str,
        download_path: str,
        high_quality: bool = False,
        title: str | None = None,
    ) -> int:
        """Download a video synchronously.

        Args:
            video_url: URL of the video to download.
            download_path: Directory path where videos will be saved.
            high_quality: If True, download up to 1080p; otherwise download mp4.
            title: Display title for notifications.

        Returns:
            Return code from the download process (0 = success).
        """
        displayed_title = title if title else video_url
        # MSG: Message shown after the download is started
        logging.info("Start downloading video from queue: %s" % displayed_title)

        cmd = self.build_download_command(
            video_url=video_url, download_path=download_path, high_quality=high_quality
        )
        logging.debug("Youtube-dl command: " + " ".join(cmd))

        rc = subprocess.call(cmd)
        if rc != 0:
            logging.error("Error code while downloading, retrying once...")
            rc = subprocess.call(cmd)
        if rc != 0:
            # MSG: Message shown after the download process is completed but the song is not found
            self.notification.log_and_send(
                _("Error downloading song: ") + displayed_title, "danger"
            )

        return rc

    def download_video_async(
        self,
        video_url: str,
        download_path: str,
        high_quality: bool = False,
        title: str | None = None,
        on_complete: Callable[[bool, str, str | None], None] | None = None,
    ) -> None:
        """Queue a video for downloading. Downloads happen sequentially.

        Args:
            video_url: URL of the video to download.
            download_path: Directory path where videos will be saved.
            high_quality: If True, download up to 1080p; otherwise download mp4.
            title: Display title for notifications.
            on_complete: Optional callback function(success, url, title) called when done.
        """
        download_task = (video_url, download_path, high_quality, title, on_complete)
        self.download_queue.put(download_task)

        displayed_title = title if title else video_url
        # MSG: Message shown when video is added to download queue
        self.notification.log_and_send(_("Downloading video: %s" % displayed_title))

    def get_queue_status(self) -> dict[str, bool | int]:
        """Return the number of videos waiting in the download queue.

        Returns:
            Dictionary with 'queued' count and 'is_downloading' status.
        """
        return {"queued": self.download_queue.qsize(), "is_downloading": self.is_downloading}

    def clear_queue(self) -> None:
        """Clear all pending downloads from the queue."""
        while not self.download_queue.empty():
            try:
                self.download_queue.get_nowait()
                self.download_queue.task_done()
            except queue.Empty:
                break
