"""Download queue manager for serialized video downloads."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import uuid
from queue import Queue
from threading import Thread

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.music_metadata import resolve_metadata
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_manager import SongManager
from pikaraoke.lib.youtube_dl import (
    build_ytdl_audio_only_command,
    build_ytdl_download_command,
    build_ytdl_video_only_command,
    get_youtube_id_from_url,
)

# yt-dlp download progress line:
# [download]   0.0% of    4.62MiB at  396.66KiB/s ETA 00:12
_YTDLP_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d+\.?\d*)%\s+of\s+.*?\s+at\s+([^\s]+)\s+ETA\s+([^\s]+)"
)

_METADATA_LOOKUP_TIMEOUT_S = 2.0


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
        return {
            "active": self.active_download,
            "pending": self.pending_downloads,
            "errors": self.download_errors,
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

                # Check if we are done with all downloads
                if self.download_queue.empty():
                    self._events.emit("download_stopped")

    def _execute_download(
        self,
        video_url: str,
        enqueue: bool,
        user: str,
        title: str | None,
    ) -> int:
        """Execute a video download.

        Dispatches to either the merged (single mp4) pipeline or the split
        (parallel audio + silent video) pipeline based on the
        ``vocal_removal`` preference. The split pipeline lets Demucs start
        processing audio as soon as yt-dlp finishes the audio stream,
        saving the wall time that would otherwise be spent waiting for
        video + the yt-dlp merge step.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).

        Returns:
            Return code from the download process (0 = success).
        """
        from flask_babel import _

        displayed_title = title if title else video_url

        # MSG: Message shown when download actually starts (after waiting in queue)
        self._events.emit("notification", _("Downloading video: %s") % displayed_title)

        # Kick off iTunes metadata resolution in parallel with yt-dlp.
        # Result is merged into info.json after yt-dlp finishes so LyricsService
        # sees canonical artist/track when it queries LRCLib.
        metadata_holder: dict = {}
        metadata_thread = Thread(
            target=self._resolve_metadata_async,
            args=(displayed_title, metadata_holder),
            name=f"metadata-lookup-{displayed_title[:40]}",
            daemon=True,
        )
        metadata_thread.start()

        video_id = get_youtube_id_from_url(video_url)
        vocal_removal = bool(self._preferences.get_or_default("vocal_removal"))

        if vocal_removal:
            rc, output = self._run_split_download(video_url, video_id)
        else:
            rc, output = self._run_merged_download(video_url)

        if rc != 0:
            # Logic removed: We no longer retry synchronously as it blocks the queue.
            # Failed downloads are now failed fast and logged.

            # MSG: Message shown after the download process is completed but the song is not found
            self._events.emit(
                "notification", _("Error downloading song: ") + displayed_title, "danger"
            )
            logging.error(f"yt-dlp stderr: {output}")
            self.download_errors.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": displayed_title,
                    "url": video_url,
                    "user": user,
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
            if video_id:
                logging.debug(f"Searching for downloaded file by ID: {video_id}")
                song_path = self._song_manager.songs.find_by_id(self._download_path, video_id)
            else:
                logging.warning("No video ID available to find downloaded song")

            if song_path:
                metadata_thread.join(timeout=_METADATA_LOOKUP_TIMEOUT_S)
                _merge_metadata_into_info_json(song_path, metadata_holder.get("meta"))
                self._events.emit("song_downloaded", song_path)
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

    def _run_merged_download(self, video_url: str) -> tuple[int | None, str]:
        """Run the upstream single-process yt-dlp (merged mp4) pipeline.

        Returns (return_code, combined stdout+stderr).
        """
        cmd = build_ytdl_download_command(
            video_url,
            self._download_path,
            self._preferences.get_or_default("high_quality"),
            self._youtubedl_proxy,
            self._additional_ytdl_args,
        )
        logging.debug("yt-dlp command: " + " ".join(cmd))
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        output_buffer: list[str] = []

        def on_progress(pct: float, speed: str, eta: str) -> None:
            if self.active_download:
                self.active_download["progress"] = pct
                self.active_download["status"] = "downloading"
                self.active_download["speed"] = speed
                self.active_download["eta"] = eta

        _read_ytdlp_stdout(process, output_buffer, on_progress)
        return process.poll(), "".join(output_buffer)

    def _run_split_download(self, video_url: str, video_id: str | None) -> tuple[int | None, str]:
        """Run audio-only and video-only yt-dlp in parallel.

        Demucs prewarm is triggered the moment the audio process exits
        cleanly, so separation starts before the video finishes
        downloading. The video return code decides overall success;
        audio failure invalidates the download (the resulting video is
        silent with no sibling to play back).

        Returns (return_code, combined stdout+stderr of both processes).
        """
        video_cmd = build_ytdl_video_only_command(
            video_url, self._download_path, self._youtubedl_proxy, self._additional_ytdl_args
        )
        audio_cmd = build_ytdl_audio_only_command(
            video_url, self._download_path, self._youtubedl_proxy, self._additional_ytdl_args
        )
        logging.debug("yt-dlp video-only: " + " ".join(video_cmd))
        logging.debug("yt-dlp audio-only: " + " ".join(audio_cmd))

        pcts = {"audio": 0.0, "video": 0.0}

        def make_on_progress(which: str):
            def on_progress(pct: float, speed: str, eta: str) -> None:
                pcts[which] = pct
                if self.active_download:
                    # Average across the two streams. Audio typically finishes
                    # well before video, so after ~50% the dial tracks the
                    # video stream's progress alone.
                    self.active_download["progress"] = (pcts["audio"] + pcts["video"]) / 2
                    self.active_download["status"] = "downloading"
                    self.active_download["speed"] = speed
                    self.active_download["eta"] = eta

            return on_progress

        # Start both Popens on the caller thread so each reader thread can
        # reach the other's handle as soon as it finishes — no races where
        # the first thread to exit tries to cancel a sibling that hasn't
        # attached its proc yet.
        audio_proc = subprocess.Popen(
            audio_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        video_proc = subprocess.Popen(
            video_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        audio_state: dict = {"rc": None, "output": ""}
        video_state: dict = {"rc": None, "output": ""}

        def cancel(proc: subprocess.Popen, label: str) -> None:
            if proc.poll() is not None:
                return
            logging.info("Cancelling %s yt-dlp after sibling failure", label)
            try:
                proc.terminate()
            except OSError as e:
                logging.warning("Failed to terminate %s yt-dlp: %s", label, e)

        def run_audio() -> None:
            buf: list[str] = []
            _read_ytdlp_stdout(audio_proc, buf, make_on_progress("audio"))
            audio_state["rc"] = audio_proc.poll()
            audio_state["output"] = "".join(buf)
            if audio_state["rc"] == 0:
                if video_id:
                    self._prewarm_audio_sibling(video_id)
            else:
                cancel(video_proc, "video")

        def run_video() -> None:
            buf: list[str] = []
            _read_ytdlp_stdout(video_proc, buf, make_on_progress("video"))
            video_state["rc"] = video_proc.poll()
            video_state["output"] = "".join(buf)
            if video_state["rc"] != 0:
                cancel(audio_proc, "audio")

        at = Thread(target=run_audio, name="yt-dlp-audio", daemon=True)
        vt = Thread(target=run_video, name="yt-dlp-video", daemon=True)
        at.start()
        vt.start()
        at.join()
        vt.join()

        combined_output = video_state["output"] + audio_state["output"]

        # If either stream failed, the download isn't usable: a silent
        # video with no audio sibling can't play, and an m4a without a
        # matching video isn't a song. Remove any orphan files and
        # surface a non-zero rc so the caller's error path runs.
        if video_state["rc"] != 0 or audio_state["rc"] != 0:
            if video_id:
                self._cleanup_split_orphans(video_id)
            rc = video_state["rc"] if video_state["rc"] not in (0, None) else audio_state["rc"]
            return rc, combined_output

        return 0, combined_output

    def _prewarm_audio_sibling(self, video_id: str) -> None:
        """Fire Demucs prewarm on the just-downloaded `.m4a` for video_id."""
        m4a = _find_file_by_id(self._download_path, video_id, ".m4a")
        if not m4a:
            logging.warning(
                "Audio download reported success but no sibling .m4a found for %s", video_id
            )
            return
        try:
            from pikaraoke.lib.demucs_processor import prewarm

            prewarm(m4a)
        except Exception:  # pragma: no cover - defensive
            logging.exception("Demucs prewarm dispatch failed for %s", m4a)

    def _cleanup_split_orphans(self, video_id: str) -> None:
        """Delete any partial video/audio files from a failed split download."""
        for ext in (".mp4", ".m4a", ".info.json"):
            path = _find_file_by_id(self._download_path, video_id, ext)
            if path:
                try:
                    os.remove(path)
                    logging.info("Removed orphan %s", path)
                except OSError as e:
                    logging.warning("Could not remove orphan %s: %s", path, e)

    @staticmethod
    def _resolve_metadata_async(title: str, holder: dict) -> None:
        try:
            holder["meta"] = resolve_metadata(title)
        except Exception:  # pragma: no cover - defensive, resolver catches its own
            logging.warning("metadata lookup crashed for %r", title, exc_info=True)


def _read_ytdlp_stdout(
    process: subprocess.Popen,
    output_buffer: list[str],
    on_progress,
) -> None:
    """Drain a yt-dlp subprocess stdout, parsing progress lines as they arrive.

    `on_progress(pct, speed, eta)` is invoked for each matching download
    line; non-progress lines are appended to ``output_buffer`` only (kept
    for error logging). Returns when the process exits and stdout is
    drained.
    """
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if not line:
            continue
        output_buffer.append(line)
        match = _YTDLP_PROGRESS_RE.search(line)
        if match:
            on_progress(float(match.group(1)), match.group(2), match.group(3))


def _find_file_by_id(directory: str, video_id: str, ext: str) -> str | None:
    """Locate ``<anything>---<video_id><ext>`` in ``directory`` (non-recursive)."""
    needle = f"---{video_id}{ext}"
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(needle):
                    return entry.path
    except OSError:
        return None
    return None


def _merge_metadata_into_info_json(song_path: str, meta: dict | None) -> None:
    """Write canonical artist/track into <stem>.info.json when absent.

    Enrichment is best-effort: any failure is logged and swallowed so it cannot
    break the download. Existing non-empty fields are preserved.
    """
    if not meta:
        return
    stem, _ext = os.path.splitext(song_path)
    info_path = f"{stem}.info.json"
    try:
        with open(info_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning("metadata enrichment: failed to read %s: %s", info_path, e)
        return
    changed = False
    for key in ("artist", "track"):
        if not (data.get(key) or "").strip() and meta.get(key):
            data[key] = meta[key]
            changed = True
    if not changed:
        return
    try:
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        logging.warning("metadata enrichment: failed to write %s: %s", info_path, e)
