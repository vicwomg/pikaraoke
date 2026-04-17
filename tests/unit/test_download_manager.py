"""Unit tests for download_manager module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.download_manager import (
    DownloadManager,
    _merge_metadata_into_info_json,
)
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager


@pytest.fixture(autouse=True)
def _stub_metadata_lookup():
    """Block real iTunes HTTP calls from every download test by default."""
    with patch("pikaraoke.lib.download_manager.resolve_metadata", return_value=None) as stub:
        yield stub


@pytest.fixture
def events():
    """Create a real EventSystem instance for testing."""
    return EventSystem()


@pytest.fixture
def preferences(tmp_path):
    """Create a real PreferenceManager instance for testing.

    Pins ``vocal_removal`` off so tests target the merged-download path by
    default (its value is host-dependent — defaults to True on machines
    with a torch-capable GPU). Tests that exercise the split-download
    pipeline flip it back on explicitly.
    """
    prefs = PreferenceManager(config_file_path=str(tmp_path / "config.ini"))
    prefs.set("vocal_removal", False)
    return prefs


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
        assert download_manager._worker_thread is None

    def test_start_creates_worker_thread(self, download_manager):
        """Test that start creates and starts a daemon thread."""
        download_manager.start()

        assert download_manager._worker_thread is not None
        assert download_manager._worker_thread.daemon is True
        assert download_manager._worker_thread.is_alive()


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

        mock_build_cmd.return_value = ["yt-dlp", "-o", "/songs/", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Starting download...", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id to return a path
        song_manager.songs.find_by_id.return_value = "/songs/Artist - Song---abc123.mp4"

        rc = download_manager._execute_download(
            "https://youtube.com/watch?v=abc123", False, "User", "Title"
        )

        assert rc == 0
        song_manager.songs.find_by_id.assert_called_once_with("/songs", "abc123")
        # add_if_valid is no longer called directly; a "song_downloaded" event is emitted instead
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
        song_manager.songs.find_by_id.return_value = "/songs/Song---abc.mp4"
        song_manager.songs.add_if_valid.return_value = True

        download_manager._execute_download(
            "https://youtube.com/watch?v=abc", True, "TestUser", "Title"
        )

        queue_manager.enqueue.assert_called_once_with(
            "/songs/Song---abc.mp4", "TestUser", log_action=False
        )

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.youtube_dl.build_ytdl_download_command")
    def test_execute_download_failure(
        self, mock_build_cmd, mock_popen, mock_run, mock_gettext, download_manager, events
    ):
        """Test download failure is handled without retry."""
        notifications = []
        events.on("notification", lambda msg, cat="info": notifications.append((msg, cat)))

        mock_build_cmd.return_value = ["yt-dlp", "url"]

        # First call (Popen) fails
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 1
        mock_popen.return_value = mock_process

        rc = download_manager._execute_download("url", False, "User", "Title")

        assert rc == 1
        # Should have "Error downloading" message with danger category
        assert any("Error downloading" in msg and cat == "danger" for msg, cat in notifications)

        # Should populate download_errors
        assert len(download_manager.download_errors) == 1
        assert download_manager.download_errors[0]["title"] == "Title"
        assert "error" in download_manager.download_errors[0]

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

        download_manager._execute_download("https://youtube.com/watch?v=abc", True, "User", "Title")

        # Should log error about queueing
        assert any("Error queueing" in msg and cat == "danger" for msg, cat in notifications)


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
            f"https://youtube.com/watch?v={video_id}",
            enqueue=True,
            user="TestUser",
            title="Test",
        )

        queue_manager.enqueue.assert_called_once_with(file_path, "TestUser", log_action=False)


class TestMergeMetadataIntoInfoJson:
    """Tests for the enrichment helper that merges iTunes results into info.json."""

    def test_fills_missing_fields(self, tmp_path):
        info = tmp_path / "Song---abc.info.json"
        info.write_text(json.dumps({"title": "noisy title", "duration": 180}))
        song_path = str(tmp_path / "Song---abc.mp4")

        _merge_metadata_into_info_json(song_path, {"artist": "Eminem", "track": "Stan"})

        data = json.loads(info.read_text())
        assert data["artist"] == "Eminem"
        assert data["track"] == "Stan"
        assert data["title"] == "noisy title"
        assert data["duration"] == 180

    def test_preserves_existing_non_empty_fields(self, tmp_path):
        info = tmp_path / "Song---abc.info.json"
        info.write_text(json.dumps({"artist": "KeepMe", "track": "KeepMeToo"}))
        song_path = str(tmp_path / "Song---abc.mp4")

        _merge_metadata_into_info_json(song_path, {"artist": "Other", "track": "Other"})

        data = json.loads(info.read_text())
        assert data["artist"] == "KeepMe"
        assert data["track"] == "KeepMeToo"

    def test_fills_only_empty_field(self, tmp_path):
        info = tmp_path / "Song---abc.info.json"
        info.write_text(json.dumps({"artist": "KeepMe", "track": ""}))
        song_path = str(tmp_path / "Song---abc.mp4")

        _merge_metadata_into_info_json(song_path, {"artist": "Other", "track": "Filled"})

        data = json.loads(info.read_text())
        assert data["artist"] == "KeepMe"
        assert data["track"] == "Filled"

    def test_no_meta_is_noop(self, tmp_path):
        info = tmp_path / "Song---abc.info.json"
        info.write_text(json.dumps({"title": "x"}))
        song_path = str(tmp_path / "Song---abc.mp4")

        _merge_metadata_into_info_json(song_path, None)

        assert json.loads(info.read_text()) == {"title": "x"}

    def test_missing_info_json_is_swallowed(self, tmp_path):
        song_path = str(tmp_path / "Song---abc.mp4")
        # Must not raise.
        _merge_metadata_into_info_json(song_path, {"artist": "A", "track": "T"})

    def test_malformed_info_json_is_swallowed(self, tmp_path):
        info = tmp_path / "Song---abc.info.json"
        info.write_text("not json {")
        song_path = str(tmp_path / "Song---abc.mp4")
        # Must not raise; leave file untouched.
        _merge_metadata_into_info_json(song_path, {"artist": "A", "track": "T"})
        assert info.read_text() == "not json {"


class TestSplitDownload:
    """Tests for the parallel audio + silent-video pipeline (vocal_removal on)."""

    @staticmethod
    def _make_popen(returncode: int = 0, lines: list[str] | None = None):
        """Return a MagicMock that behaves like a finished subprocess.Popen.

        ``readline`` drains ``lines`` and then returns ``""`` indefinitely,
        matching real stream-closed behaviour so our reader loop exits
        instead of raising StopIteration.
        """
        import itertools

        proc = MagicMock()
        queued = list(lines or [])
        proc.stdout.readline.side_effect = itertools.chain(queued, itertools.repeat(""))
        proc.poll.return_value = returncode
        return proc

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    def test_split_download_spawns_both_streams(
        self,
        mock_popen,
        mock_gettext,
        download_manager,
        preferences,
        song_manager,
        tmp_path,
    ):
        """vocal_removal on → parallel video + audio yt-dlp."""
        preferences.set("vocal_removal", True)
        download_manager._download_path = str(tmp_path)

        # Drop an m4a so _prewarm_audio_sibling has a file to operate on.
        (tmp_path / "Song---abc12345678.m4a").write_text("")
        (tmp_path / "Song---abc12345678.mp4").write_text("")
        song_manager.songs.find_by_id.return_value = str(tmp_path / "Song---abc12345678.mp4")

        mock_popen.side_effect = [self._make_popen(0), self._make_popen(0)]

        with patch("pikaraoke.lib.demucs_processor.prewarm") as mock_prewarm:
            rc = download_manager._execute_download(
                "https://youtube.com/watch?v=abc12345678",
                enqueue=False,
                user="User",
                title="Title",
            )

        assert rc == 0
        # Two Popens — one video, one audio.
        assert mock_popen.call_count == 2
        commands = [call.args[0] for call in mock_popen.call_args_list]
        joined = [" ".join(c) for c in commands]
        assert any("bestvideo" in c for c in joined)
        assert any("bestaudio" in c for c in joined)
        # Demucs prewarm fired on the m4a sibling.
        mock_prewarm.assert_called_once()
        assert mock_prewarm.call_args.args[0].endswith(".m4a")

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    def test_split_download_audio_failure_cleans_up_orphans(
        self,
        mock_popen,
        mock_gettext,
        download_manager,
        preferences,
        tmp_path,
    ):
        """Audio stream failure invalidates the download and removes siblings."""
        preferences.set("vocal_removal", True)
        download_manager._download_path = str(tmp_path)

        mp4 = tmp_path / "Song---abc12345678.mp4"
        info = tmp_path / "Song---abc12345678.info.json"
        mp4.write_text("")
        info.write_text("{}")

        # Video succeeds (rc=0), audio fails (rc=1). Order matters less than
        # which file each returns — side_effect serves them in sequence.
        mock_popen.side_effect = [self._make_popen(0), self._make_popen(1)]

        rc = download_manager._execute_download(
            "https://youtube.com/watch?v=abc12345678",
            enqueue=False,
            user="User",
            title="Title",
        )

        assert rc != 0
        # Silent video and info.json are swept up so the library doesn't
        # index an unplayable song.
        assert not mp4.exists()
        assert not info.exists()
        assert len(download_manager.download_errors) == 1

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    def test_split_download_progress_is_averaged(
        self,
        mock_popen,
        mock_gettext,
        download_manager,
        preferences,
        tmp_path,
    ):
        """active_download.progress tracks the mean of the two streams."""
        preferences.set("vocal_removal", True)
        download_manager._download_path = str(tmp_path)
        download_manager.active_download = {
            "title": "t",
            "progress": 0.0,
            "status": "starting",
            "speed": "",
            "eta": "",
        }

        # Video at 80%, audio at 40% → averaged 60%. Mock stdout emits one
        # progress line each so _read_ytdlp_stdout sees something to parse.
        video_proc = self._make_popen(
            0,
            lines=[
                "[download]  80.0% of   10.00MiB at 1.00MiB/s ETA 00:05\n",
            ],
        )
        audio_proc = self._make_popen(
            0,
            lines=[
                "[download]  40.0% of    1.00MiB at 500KiB/s ETA 00:01\n",
            ],
        )
        mock_popen.side_effect = [video_proc, audio_proc]

        download_manager._run_split_download("https://youtube.com/watch?v=abc", "abc12345678")

        # Both progress lines landed; progress is the mean.
        assert download_manager.active_download["progress"] == pytest.approx(60.0, abs=0.01)


class TestParallelMetadataEnrichment:
    """Tests that _execute_download integrates enrichment with the download flow."""

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_enrichment_merged_into_info_json(
        self,
        mock_build_cmd,
        mock_popen,
        mock_gettext,
        download_manager,
        song_manager,
        tmp_path,
        _stub_metadata_lookup,
    ):
        _stub_metadata_lookup.return_value = {"artist": "Eminem", "track": "Stan"}

        mock_build_cmd.return_value = ["yt-dlp", "url"]
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["ok", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        song_path = tmp_path / "Song---abc12345678.mp4"
        song_path.write_text("")
        info = tmp_path / "Song---abc12345678.info.json"
        info.write_text(json.dumps({"title": "Eminem - Stan (Long Version) ft. Dido"}))
        song_manager.songs.find_by_id.return_value = str(song_path)

        download_manager._execute_download(
            "https://youtube.com/watch?v=abc12345678", False, "User", "noisy title"
        )

        data = json.loads(info.read_text())
        assert data["artist"] == "Eminem"
        assert data["track"] == "Stan"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_enrichment_none_leaves_info_json_untouched(
        self,
        mock_build_cmd,
        mock_popen,
        mock_gettext,
        download_manager,
        song_manager,
        tmp_path,
    ):
        # resolver stub returns None via autouse fixture.
        mock_build_cmd.return_value = ["yt-dlp", "url"]
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["ok", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        song_path = tmp_path / "Song---abc12345678.mp4"
        song_path.write_text("")
        info = tmp_path / "Song---abc12345678.info.json"
        original = json.dumps({"title": "Something"})
        info.write_text(original)
        song_manager.songs.find_by_id.return_value = str(song_path)

        download_manager._execute_download(
            "https://youtube.com/watch?v=abc12345678", False, "User", "Title"
        )

        assert info.read_text() == original

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_download_succeeds_even_if_resolver_raises(
        self,
        mock_build_cmd,
        mock_popen,
        mock_gettext,
        download_manager,
        song_manager,
        tmp_path,
        _stub_metadata_lookup,
    ):
        _stub_metadata_lookup.side_effect = RuntimeError("boom")

        mock_build_cmd.return_value = ["yt-dlp", "url"]
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["ok", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        song_manager.songs.find_by_id.return_value = str(tmp_path / "Song---x.mp4")

        rc = download_manager._execute_download(
            "https://youtube.com/watch?v=abc12345678", False, "User", "Title"
        )

        assert rc == 0
