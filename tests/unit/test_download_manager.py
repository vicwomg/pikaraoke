"""Unit tests for download_manager module."""

from time import monotonic
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.download_manager import (
    MAX_DOWNLOAD_ATTEMPTS,
    STALE_PROGRESS_SECONDS,
    DownloadManager,
    _summarise_ytdl_failure,
)
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager


def make_request(
    url: str = "https://youtube.com/watch?v=dQw4w9WgXcQ",
    enqueue: bool = False,
    user: str = "User",
    title: str = "Title",
) -> dict:
    """Build a download request in the shape queue_download produces."""
    return {
        "video_url": url,
        "enqueue": enqueue,
        "user": user,
        "title": title,
        "display_title": title or url,
        "attempts": 0,
    }


@pytest.fixture(autouse=True)
def no_reprioritize():
    """Keep psutil away from whatever pid a mocked Popen invents."""
    with patch("pikaraoke.lib.download_manager._use_spare_capacity"):
        yield


@pytest.fixture
def events():
    """Create a real EventSystem instance for testing."""
    return EventSystem()


@pytest.fixture
def preferences():
    """Create a real PreferenceManager instance for testing."""
    return PreferenceManager()


@pytest.fixture
def song_manager():
    """Create a mock SongManager."""
    mock = MagicMock()
    mock.songs = MagicMock()
    return mock


@pytest.fixture
def queue_manager():
    """Create a mock QueueManager."""
    return MagicMock()


@pytest.fixture
def download_manager(events, preferences, song_manager, queue_manager):
    """Create a DownloadManager with real Events/Prefs and mocked managers."""
    return DownloadManager(
        events=events,
        preferences=preferences,
        song_manager=song_manager,
        queue_manager=queue_manager,
        download_path="/songs",
        youtubedl_proxy=None,
        additional_ytdl_args=None,
    )


class TestDownloadManagerInit:
    """Tests for DownloadManager initialization."""

    def test_init_creates_queue(self, download_manager):
        """Test that init creates an empty queue."""
        assert download_manager.download_queue.empty()
        assert download_manager._is_downloading is False
        assert download_manager._worker is None

    def test_start_creates_worker_greenlet(self, download_manager):
        """Test that start spawns a live worker greenlet."""
        download_manager.start()

        assert download_manager._worker is not None
        assert not download_manager._worker.dead

        # Reap the idle worker so it can't block the suite on the (unpatched) queue
        download_manager._worker.kill()


class TestDownloadManagerQueueDownload:
    """Tests for DownloadManager.queue_download method."""

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_queue_download_first_item(self, mock_gettext, download_manager, events):
        """Test queueing first download shows 'starting' message and emits event."""
        notifications = []
        events.on("notification", lambda msg, *args: notifications.append(msg))

        download_events = []
        events.on("download_started", lambda: download_events.append("started"))

        download_manager.queue_download("https://youtube.com/watch?v=test", user="TestUser")

        assert download_manager.download_queue.qsize() == 1
        assert len(notifications) == 1
        assert "Download starting" in notifications[0]
        assert len(download_events) == 1

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_queue_download_with_pending(self, mock_gettext, download_manager, events):
        """Test queueing when items are pending shows queue position."""
        notifications = []
        events.on("notification", lambda msg, *args: notifications.append(msg))

        download_manager._is_downloading = True  # Simulate active download

        download_manager.queue_download("https://youtube.com/watch?v=test", user="TestUser")

        assert len(notifications) == 1
        assert "Download queued" in notifications[0]

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_queue_download_with_title(self, mock_gettext, download_manager, events):
        """Test queueing with custom title uses title in message."""
        notifications = []
        events.on("notification", lambda msg, *args: notifications.append(msg))

        download_manager.queue_download(
            "https://youtube.com/watch?v=test",
            title="My Custom Title",
            user="TestUser",
        )

        assert len(notifications) == 1
        assert "My Custom Title" in notifications[0]

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_queue_download_stores_request_data(self, mock_gettext, download_manager):
        """Test that queue stores all request data."""
        download_manager.queue_download(
            "https://youtube.com/watch?v=test123",
            enqueue=True,
            user="TestUser",
            title="Test Song",
        )

        item = download_manager.download_queue.get_nowait()
        assert item["video_url"] == "https://youtube.com/watch?v=test123"
        assert item["enqueue"] is True
        assert item["user"] == "TestUser"
        assert item["title"] == "Test Song"

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_queue_download_strips_playlist_param(self, mock_gettext, download_manager):
        """Test that playlist parameter is stripped from URL."""
        download_manager.queue_download(
            "https://youtube.com/watch?v=test123&list=PLxxx",
            user="TestUser",
        )

        item = download_manager.download_queue.get_nowait()
        assert item["video_url"] == "https://youtube.com/watch?v=test123"


class TestDownloadManagerExecuteDownload:
    """Tests for DownloadManager._execute_download method."""

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_success(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager, song_manager, events
    ):
        """Test successful download execution."""
        notifications = []
        events.on("notification", lambda msg, *args: notifications.append(msg))
        downloaded = []
        events.on("song_downloaded", lambda path, video_id: downloaded.append((path, video_id)))

        mock_build_cmd.return_value = ["yt-dlp", "-o", "/songs/", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Starting download...", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id to return a path
        song_manager.songs.find_by_id.return_value = "/songs/Artist - Song---dQw4w9WgXcQ.mp4"

        rc = download_manager._execute_download(make_request())

        assert rc == 0
        song_manager.songs.find_by_id.assert_called_once_with("/songs", "dQw4w9WgXcQ")
        # The library registers the song from this event, and the search page keys
        # its row rewrite off the id, so both halves of the payload matter.
        assert downloaded == [("/songs/Artist - Song---dQw4w9WgXcQ.mp4", "dQw4w9WgXcQ")]
        assert any("Downloaded" in n for n in notifications)

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_with_enqueue(
        self,
        mock_build_cmd,
        mock_popen,
        mock_gettext,
        download_manager,
        song_manager,
        queue_manager,
    ):
        """Test download with enqueue adds to queue."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Starting download...", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id
        song_manager.songs.find_by_id.return_value = "/songs/Song---dQw4w9WgXcQ.mp4"
        song_manager.songs.add_if_valid.return_value = True

        download_manager._execute_download(make_request(enqueue=True, user="TestUser"))

        queue_manager.enqueue.assert_called_once_with(
            "/songs/Song---dQw4w9WgXcQ.mp4", "TestUser", log_action=False
        )

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_enqueue_without_path(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager, song_manager, events
    ):
        """Test enqueue fails gracefully when path can't be parsed."""
        notifications = []
        events.on("notification", lambda msg, cat="info": notifications.append((msg, cat)))

        mock_build_cmd.return_value = ["yt-dlp", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["No parseable path in output", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id to return None (file not found)
        song_manager.songs.find_by_id.return_value = None

        download_manager._execute_download(
            make_request(url="https://youtube.com/watch?v=abc", enqueue=True)
        )

        # Should log error about queueing
        assert any("Error queueing" in msg and cat == "danger" for msg, cat in notifications)


class TestDownloadManagerRetry:
    """Tests for re-queueing failed downloads instead of failing them outright."""

    @staticmethod
    def _failing_popen(mock_popen):
        process = MagicMock()
        process.stdout.readline.return_value = ""
        process.poll.return_value = 1
        mock_popen.return_value = process
        return process

    @staticmethod
    def _drain(manager) -> int:
        """Run the queue to exhaustion the way the worker greenlet does."""
        runs = 0
        while not manager.download_queue.empty():
            request = manager.download_queue.get()
            manager.pending_downloads.pop(0)
            manager._execute_download(request)
            runs += 1
        return runs

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_requeues_on_failure(self, mock_build_cmd, mock_popen, mock_gettext, download_manager):
        """A first failure goes back on the queue rather than to an error card."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        self._failing_popen(mock_popen)

        rc = download_manager._execute_download(make_request())

        assert rc == 1
        assert download_manager.download_errors == []
        assert download_manager.download_queue.get_nowait()["attempts"] == 1
        assert len(download_manager.pending_downloads) == 1

    def test_retry_clears_the_stale_rate(self, download_manager):
        """The dead attempt's speed and ETA would otherwise sit under a bar back at zero."""
        download_manager.active_download = {
            "progress": 62.0,
            "status": "downloading",
            "speed": "2.31MiB/s",
            "eta": "0:04",
            "attempts": 0,
        }

        assert download_manager._requeue(make_request()) is True

        assert download_manager.active_download["progress"] == 0.0
        assert download_manager.active_download["status"] == "retrying"
        assert download_manager.active_download["speed"] == "---"
        assert download_manager.active_download["eta"] == "--:--"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_stops_at_attempt_cap(self, mock_build_cmd, mock_popen, mock_gettext, download_manager):
        """A download that never succeeds is bounded, and its error keeps enqueue."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        self._failing_popen(mock_popen)

        download_manager.queue_download(
            "https://youtube.com/watch?v=dQw4w9WgXcQ", enqueue=True, title="Broken"
        )

        assert self._drain(download_manager) == MAX_DOWNLOAD_ATTEMPTS
        assert mock_popen.call_count == MAX_DOWNLOAD_ATTEMPTS
        assert download_manager.pending_downloads == []
        assert len(download_manager.download_errors) == 1
        # Without this the Retry button silently drops "add to the playback queue"
        assert download_manager.download_errors[0]["enqueue"] is True

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen", side_effect=OSError("cannot spawn"))
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_invocation_crash_becomes_a_visible_failure(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager
    ):
        """A crash launching yt-dlp must retry and surface, not vanish out of the worker."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]

        download_manager.queue_download("https://youtube.com/watch?v=dQw4w9WgXcQ", title="Broken")

        assert self._drain(download_manager) == MAX_DOWNLOAD_ATTEMPTS
        assert len(download_manager.download_errors) == 1
        assert "OSError" in download_manager.download_errors[0]["error"]

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_retry_is_silent(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager, events
    ):
        """A retried attempt must not repeat the 'Downloading video' toast."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        self._failing_popen(mock_popen)

        notifications = []
        events.on("notification", lambda msg, *args: notifications.append(msg))

        download_manager.queue_download("https://youtube.com/watch?v=dQw4w9WgXcQ", title="Broken")
        self._drain(download_manager)

        assert sum("Downloading video" in n for n in notifications) == 1

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_no_retry_on_success(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager, song_manager
    ):
        """Guards against doubling every download."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        process = MagicMock()
        process.stdout.readline.side_effect = ["Done", ""]
        process.poll.return_value = 0
        mock_popen.return_value = process
        song_manager.songs.find_by_id.return_value = "/songs/Song---dQw4w9WgXcQ.mp4"

        download_manager.queue_download("https://youtube.com/watch?v=dQw4w9WgXcQ", title="Fine")

        assert self._drain(download_manager) == 1
        assert mock_popen.call_count == 1

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_retry_goes_to_the_back_of_the_queue(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager
    ):
        """Back-of-queue placement is the backoff: waiting downloads overtake the failure."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        self._failing_popen(mock_popen)

        download_manager.queue_download("https://youtube.com/watch?v=aaaaaaaaaaa", title="Broken")
        download_manager.queue_download("https://youtube.com/watch?v=bbbbbbbbbbb", title="Waiting")

        request = download_manager.download_queue.get()
        download_manager.pending_downloads.pop(0)
        download_manager._execute_download(request)

        assert [r["title"] for r in download_manager.pending_downloads] == ["Waiting", "Broken"]
        assert download_manager.download_queue.get_nowait()["title"] == "Waiting"
        assert download_manager.download_queue.get_nowait()["title"] == "Broken"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_error_card_carries_only_the_error_line(
        self, mock_build_cmd, mock_popen, mock_gettext, download_manager
    ):
        """The card shows the diagnosis, not the whole transcript that buried it."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        lines = [
            'WARNING: "-f mp4" selects the best pre-merged mp4 format\n',
            "[info] fAzCnCwJO5c: Downloading 1 format(s): 18\n",
            "ERROR: unable to download video data: HTTP Error 403: Forbidden\n",
        ]
        process = MagicMock()
        process.stdout.readline.side_effect = (lines + [""]) * MAX_DOWNLOAD_ATTEMPTS
        process.poll.return_value = 1
        mock_popen.return_value = process

        download_manager.queue_download("https://youtube.com/watch?v=fAzCnCwJO5c", title="Broken")
        self._drain(download_manager)

        assert (
            download_manager.download_errors[0]["error"]
            == "unable to download video data: HTTP Error 403: Forbidden"
        )

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_retry_error_requeues_and_clears(self, mock_gettext, download_manager):
        """The Retry button restores the request, enqueue flag included."""
        download_manager.download_errors = [
            {
                "id": "err-1",
                "title": "Failed Song",
                "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
                "user": "TestUser",
                "enqueue": True,
                "error": "HTTP Error 403: Forbidden",
            }
        ]

        assert download_manager.retry_error("err-1") is True
        assert download_manager.download_errors == []

        request = download_manager.download_queue.get_nowait()
        assert request["video_url"] == "https://youtube.com/watch?v=dQw4w9WgXcQ"
        assert request["enqueue"] is True
        assert request["user"] == "TestUser"
        assert request["attempts"] == 0

    def test_retry_error_unknown_id(self, download_manager):
        """An already-dismissed error cannot be retried."""
        assert download_manager.retry_error("nope") is False
        assert download_manager.download_queue.empty()


class TestSummariseYtdlFailure:
    """Tests for reducing yt-dlp's transcript to the line that names the failure."""

    # Verbatim from a real 403, whose warning block pushed the diagnosis past truncation.
    REAL_OUTPUT = (
        '\nWARNING: "-f mp4" selects the best pre-merged mp4 format which is often not'
        " what's intended.\n"
        "         Pre-merged mp4 formats are not available from all sites, or may only be"
        " available in lower quality.\n"
        "[youtube] Extracting URL: https://www.youtube.com/watch?v=fAzCnCwJO5c\n"
        "[youtube] fAzCnCwJO5c: Downloading android vr player API JSON\n"
        "[info] fAzCnCwJO5c: Downloading 1 format(s): 18\n"
        "ERROR: unable to download video data: HTTP Error 403: Forbidden\n"
    )

    def test_picks_the_error_line_out_of_the_transcript(self):
        assert (
            _summarise_ytdl_failure(self.REAL_OUTPUT)
            == "unable to download video data: HTTP Error 403: Forbidden"
        )

    def test_falls_back_to_the_last_line_when_nothing_is_flagged(self):
        """yt-dlp can die without an ERROR: line; the card still needs something."""
        assert _summarise_ytdl_failure("[youtube] Extracting URL: x\nkilled\n") == "killed"

    def test_empty_output(self):
        assert _summarise_ytdl_failure("") == "Unknown error"

    def test_fallback_skips_the_progress_templates(self):
        """A download killed mid-transfer ends on a bar line, which names no failure."""
        killed = (
            "[info] abc: Downloading 1 format(s): 136+140\n"
            "[pk-size]|[12577843, 4205572]\n"
            "[pk]| 43.2%|Unknown B/s|Unknown ETA|avc1.640028\n"
        )
        assert _summarise_ytdl_failure(killed) == "[info] abc: Downloading 1 format(s): 136+140"


class TestDownloadProgress:
    """Tests for folding yt-dlp's templated progress lines into the bar."""

    TWO_PASS_CMD = ["yt-dlp", "-f", "bestvideo[height<=1080]+bestaudio/best[ext!=webm]", "url"]
    ONE_PASS_CMD = ["yt-dlp", "-f", "mp4", "url"]

    @staticmethod
    def progress_line(percent="50.0%", speed="1.20MiB/s", eta="00:12", vcodec="avc1.640028"):
        """One download line in the shape the --progress-template produces."""
        return f"[pk]|{percent}|{speed}|{eta}|{vcodec}\n"

    def run_lines(self, download_manager, cmd, lines):
        """Feed lines through _run_ytdl, snapshotting active_download after each one."""
        download_manager.active_download = {
            "progress": 0.0,
            "status": "starting",
            "speed": "---",
            "eta": "--:--",
        }
        remaining = list(lines) + [""]
        snapshots = []

        def readline():
            snapshots.append(dict(download_manager.active_download))
            return remaining.pop(0)

        process = MagicMock()
        process.stdout.readline.side_effect = readline
        process.poll.return_value = 0
        with patch("subprocess.Popen", return_value=process):
            download_manager._run_ytdl(cmd)
        snapshots.append(dict(download_manager.active_download))
        return snapshots

    def test_two_passes_share_one_scale(self, download_manager):
        """The audio pass resumes where the video pass stopped instead of rewinding."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            [
                self.progress_line("0.0%"),
                self.progress_line("100.0%"),
                self.progress_line("0.0%", vcodec="none"),
                self.progress_line("100.0%", vcodec="none"),
            ],
        )
        progress = [snap["progress"] for snap in snapshots]
        assert progress == sorted(progress)
        assert 90.0 in progress and progress[-1] == 100.0

    def test_single_pass_reaches_the_top_of_the_scale(self, download_manager):
        """With no audio pass, the video runs the whole scale on its own."""
        snapshots = self.run_lines(
            download_manager, self.ONE_PASS_CMD, [self.progress_line("100.0%")]
        )
        assert snapshots[-1]["progress"] == 100.0

    def test_audio_pass_is_named_by_vcodec(self, download_manager):
        """vcodec is the discriminator, not a percentage that reset."""
        snapshots = self.run_lines(
            download_manager, self.TWO_PASS_CMD, [self.progress_line("50.0%", vcodec="none")]
        )
        assert snapshots[-1]["status"] == "downloading audio"
        assert snapshots[-1]["progress"] == 95.0

    def test_missing_estimates_survive_the_split(self, download_manager):
        """The speed carries a space when yt-dlp is guessing, so the delimiter is a pipe."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            [self.progress_line("10.0%", speed="Unknown B/s", eta="Unknown")],
        )
        assert snapshots[-1]["speed"] == "Unknown B/s"
        assert snapshots[-1]["eta"] == "Unknown"
        assert snapshots[-1]["progress"] == 9.0

    def test_unparseable_percent_holds_the_bar(self, download_manager):
        """A percentage yt-dlp cannot compute must not drop the bar back to zero."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            [self.progress_line("50.0%"), self.progress_line("Unknown%", speed="10MiB/s")],
        )
        assert snapshots[-1]["progress"] == 45.0
        assert snapshots[-1]["speed"] == "10MiB/s"

    def test_merge_holds_a_full_bar(self, download_manager):
        """Every byte is down by the merge, which reports no percentage of its own."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            [self.progress_line("50.0%", vcodec="none"), "[pk-post]|Merger\n"],
        )
        assert snapshots[-1]["progress"] == 100.0
        assert snapshots[-1]["status"] == "merging"
        assert snapshots[-1]["speed"] == "---"
        assert snapshots[-1]["eta"] == "--:--"

    def test_real_sizes_place_the_seam(self, download_manager):
        """The formats' byte counts replace the guessed split between the passes."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            [
                "[pk-size]|[12577843, 4205572]\n",
                self.progress_line("100.0%"),
                self.progress_line("100.0%", vcodec="none"),
            ],
        )
        progress = [snap["progress"] for snap in snapshots]
        # Video is 75% of 16.8MB, so the seam sits well below the 90.0 fallback.
        assert 74.9 in progress
        assert progress == sorted(progress)
        assert progress[-1] == 100.0

    def test_unusable_sizes_keep_the_fallback_seam(self, download_manager):
        """A single-file download has no requested_formats, so yt-dlp prints NA."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            ["[pk-size]|NA\n", self.progress_line("100.0%")],
        )
        assert snapshots[-1]["progress"] == 90.0

    def test_other_output_is_ignored(self, download_manager):
        """yt-dlp's prose still flows past; only the templated lines count."""
        snapshots = self.run_lines(
            download_manager,
            self.TWO_PASS_CMD,
            ["[youtube] test123: Downloading android vr player API JSON\n"],
        )
        assert snapshots[-1]["status"] == "starting"
        assert snapshots[-1]["progress"] == 0.0


class TestStalledProgress:
    """The panel must stop reporting a rate once yt-dlp stops printing one.

    The seam between the video and audio passes prints nothing while the next
    connection opens, and the bar sat there showing the finished pass's speed and ETA.
    """

    @staticmethod
    def downloading(seconds_ago: float, status: str = "downloading") -> dict:
        return {
            "title": "Song",
            "progress": 75.0,
            "status": status,
            "speed": "2.31MiB/s",
            "eta": "0:04",
            "progress_at": monotonic() - seconds_ago,
        }

    def test_a_fresh_line_is_not_stale(self, download_manager):
        download_manager.active_download = self.downloading(0.0)

        assert download_manager.get_downloads_status()["active"]["stalled"] is False

    def test_silence_marks_the_rate_stale(self, download_manager):
        download_manager.active_download = self.downloading(STALE_PROGRESS_SECONDS + 0.5)

        active = download_manager.get_downloads_status()["active"]

        assert active["stalled"] is True
        # The bytes are down either way, so the bar must not give up its place.
        assert active["progress"] == 75.0

    def test_the_audio_pass_can_stall_too(self, download_manager):
        download_manager.active_download = self.downloading(
            STALE_PROGRESS_SECONDS + 0.5, status="downloading audio"
        )

        assert download_manager.get_downloads_status()["active"]["stalled"] is True

    def test_phases_that_report_no_rate_never_stall(self, download_manager):
        """The merge already animates in place; marking it stalled would say it twice."""
        for status in ("starting", "retrying", "merging", "complete"):
            download_manager.active_download = self.downloading(60.0, status=status)

            assert download_manager.get_downloads_status()["active"]["stalled"] is False, status

    def test_the_internal_timestamp_stays_out_of_the_payload(self, download_manager):
        download_manager.active_download = self.downloading(0.0)

        assert "progress_at" not in download_manager.get_downloads_status()["active"]

    def test_a_progress_line_refreshes_the_stamp(self, download_manager):
        """Without this every reading past the first 1.5 s would read as stalled."""
        download_manager.active_download = self.downloading(60.0)

        download_manager._apply_progress_line("[pk]| 80.0%|3.10MiB/s|0:02|avc1.640028", 100.0)

        assert download_manager.get_downloads_status()["active"]["stalled"] is False


class TestDownloadManagerStatus:
    """Tests for DownloadManager.get_downloads_status method."""

    def test_get_downloads_status_empty(self, download_manager):
        """Test status with no downloads."""
        status = download_manager.get_downloads_status()

        assert status["active"] is None
        assert status["pending"] == []

    def test_get_downloads_status_pending(self, download_manager):
        """Test status with pending downloads."""
        download_manager.queue_download("http://example.com/1", title="Song 1")
        download_manager.queue_download("http://example.com/2", title="Song 2")

        status = download_manager.get_downloads_status()

        assert status["active"] is None
        assert len(status["pending"]) == 2
        assert status["pending"][0]["title"] == "Song 1"
        assert status["pending"][1]["title"] == "Song 2"

    def test_get_downloads_status_active(self, download_manager):
        """Test status with active download."""
        # Simulate active download
        download_manager.active_download = {
            "title": "Active Song",
            "progress": 50.0,
            "status": "downloading",
        }

        status = download_manager.get_downloads_status()

        assert status["active"]["title"] == "Active Song"
        assert status["active"]["progress"] == 50.0

    def test_get_downloads_status_errors(self, download_manager):
        """Test status with download errors."""
        download_manager.download_errors = [
            {
                "id": "1234",
                "title": "Failed Song",
                "url": "http://example.com/fail",
                "user": "User",
                "error": "Error message",
            }
        ]

        status = download_manager.get_downloads_status()

        assert len(status["errors"]) == 1
        assert status["errors"][0]["title"] == "Failed Song"

    def test_remove_error(self, download_manager):
        """Test removing an error by ID."""
        download_manager.download_errors = [
            {"id": "1234", "title": "Failed Song", "error": "Error"}
        ]

        # Test remove invalid ID
        result = download_manager.remove_error("9999")
        assert result is False
        assert len(download_manager.download_errors) == 1

        # Test remove valid ID
        result = download_manager.remove_error("1234")
        assert result is True
        assert len(download_manager.download_errors) == 0


class TestDownloadManagerSpecialCharacters:
    """Tests for handling special characters in downloaded filenames.

    These tests prevent regressions where special characters in song titles
    (common in non-English songs) break the enqueue functionality.
    See commit f399b57 for the original fix.
    """

    @pytest.mark.parametrize(
        "video_id,file_path",
        [
            ("abc12345678", "/songs/Babymetal - ギミチョコ---abc12345678.mp4"),
            ("xyz98765432", "/songs/BTS - 봄날---xyz98765432.mp4"),
            ("def456789ab", "/songs/Tom & Jerry - What's Up---def456789ab.mp4"),
        ],
        ids=["japanese", "korean", "special_chars"],
    )
    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_special_characters_enqueue(
        self,
        mock_build_cmd,
        mock_popen,
        mock_gettext,
        video_id,
        file_path,
        download_manager,
        song_manager,
        queue_manager,
    ):
        """Test enqueue works with special characters in filename."""
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Done", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        song_manager.songs.find_by_id.return_value = file_path
        song_manager.songs.add_if_valid.return_value = True

        download_manager._execute_download(
            make_request(
                url=f"https://youtube.com/watch?v={video_id}",
                enqueue=True,
                user="TestUser",
                title="Test",
            )
        )

        queue_manager.enqueue.assert_called_once_with(file_path, "TestUser", log_action=False)
