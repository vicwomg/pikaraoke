"""Unit tests for download_manager module."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.download_manager import DownloadManager


class MockKaraokeForDownload:
    """Mock Karaoke instance for DownloadManager tests."""

    def __init__(self):
        self.download_path = "/songs/"
        self.high_quality = False
        self.youtubedl_proxy = None
        self.additional_ytdl_args = None
        self.available_songs = MagicMock()
        self.log_and_send = MagicMock()
        self.queue_manager = MagicMock()


class TestDownloadManagerInit:
    """Tests for DownloadManager initialization."""

    def test_init_creates_queue(self):
        """Test that init creates an empty queue."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        assert dm.karaoke == mock_karaoke
        assert dm.download_queue.empty()
        assert dm._is_downloading is False
        assert dm._worker_thread is None

    def test_start_creates_worker_thread(self):
        """Test that start creates and starts a daemon thread."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.start()

        assert dm._worker_thread is not None
        assert dm._worker_thread.daemon is True
        assert dm._worker_thread.is_alive()


class TestDownloadManagerQueueDownload:
    """Tests for DownloadManager.queue_download method."""

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.download_manager._broadcast_helper")
    def test_queue_download_first_item(self, mock_broadcast, mock_gettext):
        """Test queueing first download shows 'starting' message."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.queue_download("https://youtube.com/watch?v=test", user="TestUser")

        assert dm.download_queue.qsize() == 1
        mock_karaoke.log_and_send.assert_called_once()
        call_arg = mock_karaoke.log_and_send.call_args[0][0]
        assert "Download starting" in call_arg
        mock_broadcast.assert_called_once_with(dm.app, "download_started")

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.download_manager._broadcast_helper")
    def test_queue_download_with_pending(self, mock_broadcast, mock_gettext):
        """Test queueing when items are pending shows queue position."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)
        dm._is_downloading = True  # Simulate active download

        dm.queue_download("https://youtube.com/watch?v=test", user="TestUser")

        call_arg = mock_karaoke.log_and_send.call_args[0][0]
        assert "Download queued" in call_arg

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.download_manager._broadcast_helper")
    def test_queue_download_with_title(self, mock_broadcast, mock_gettext):
        """Test queueing with custom title uses title in message."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.queue_download(
            "https://youtube.com/watch?v=test",
            title="My Custom Title",
            user="TestUser",
        )

        call_arg = mock_karaoke.log_and_send.call_args[0][0]
        assert "My Custom Title" in call_arg

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.download_manager._broadcast_helper")
    def test_queue_download_stores_request_data(self, mock_broadcast, mock_gettext):
        """Test that queue stores all request data."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.queue_download(
            "https://youtube.com/watch?v=test123",
            enqueue=True,
            user="TestUser",
            title="Test Song",
        )

        item = dm.download_queue.get_nowait()
        assert item["video_url"] == "https://youtube.com/watch?v=test123"
        assert item["enqueue"] is True
        assert item["user"] == "TestUser"
        assert item["title"] == "Test Song"


class TestDownloadManagerExecuteDownload:
    """Tests for DownloadManager._execute_download method."""

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_success(self, mock_build_cmd, mock_popen, mock_gettext):
        """Test successful download execution."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        mock_build_cmd.return_value = ["yt-dlp", "-o", "/songs/", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Starting download...", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id to return a path
        mock_karaoke.available_songs.find_by_id.return_value = "/songs/Artist - Song---abc123.mp4"

        rc = dm._execute_download("https://youtube.com/watch?v=abc123", False, "User", "Title")

        assert rc == 0
        mock_karaoke.available_songs.find_by_id.assert_called_once_with("/songs/", "abc123")
        mock_karaoke.available_songs.add_if_valid.assert_called_once_with(
            "/songs/Artist - Song---abc123.mp4"
        )

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_with_enqueue(self, mock_build_cmd, mock_popen, mock_gettext):
        """Test download with enqueue adds to queue."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        mock_build_cmd.return_value = ["yt-dlp", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Starting download...", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id
        mock_karaoke.available_songs.find_by_id.return_value = "/songs/Song---abc.mp4"
        mock_karaoke.available_songs.add_if_valid.return_value = True

        dm._execute_download("https://youtube.com/watch?v=abc", True, "TestUser", "Title")

        mock_karaoke.queue_manager.enqueue.assert_called_once_with(
            "/songs/Song---abc.mp4", "TestUser", log_action=False
        )

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.youtube_dl.build_ytdl_download_command")
    def test_execute_download_failure(self, mock_build_cmd, mock_popen, mock_run, mock_gettext):
        """Test download failure is handled without retry."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        mock_build_cmd.return_value = ["yt-dlp", "url"]

        # First call (Popen) fails
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = 1
        mock_popen.return_value = mock_process

        rc = dm._execute_download("url", False, "User", "Title")

        assert rc == 1
        # Should have "Error downloading" message
        calls = mock_karaoke.log_and_send.call_args_list
        assert any("Error downloading" in str(call) for call in calls)

        # Should populate download_errors
        assert len(dm.download_errors) == 1
        assert dm.download_errors[0]["title"] == "Title"
        # Error content depends on mock, here likely empty string if not set explicitly, or check length
        assert "error" in dm.download_errors[0]

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("subprocess.Popen")
    @patch("pikaraoke.lib.download_manager.build_ytdl_download_command")
    def test_execute_download_enqueue_without_path(self, mock_build_cmd, mock_popen, mock_gettext):
        """Test enqueue fails gracefully when path can't be parsed."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        mock_build_cmd.return_value = ["yt-dlp", "url"]

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["No parseable path in output", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        # Mock find_by_id to return None (file not found)
        mock_karaoke.available_songs.find_by_id.return_value = None

        dm._execute_download("https://youtube.com/watch?v=abc", True, "User", "Title")

        # Should log error about queueing
        calls = mock_karaoke.log_and_send.call_args_list
        assert any("Error queueing" in str(call) for call in calls)


class TestDownloadManagerStatus:
    """Tests for DownloadManager.get_downloads_status method."""

    def test_get_downloads_status_empty(self):
        """Test status with no downloads."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        status = dm.get_downloads_status()

        assert status["active"] is None
        assert status["pending"] == []

    @patch("pikaraoke.lib.download_manager._broadcast_helper")
    def test_get_downloads_status_pending(self, mock_broadcast):
        """Test status with pending downloads."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.queue_download("http://example.com/1", title="Song 1")
        dm.queue_download("http://example.com/2", title="Song 2")

        status = dm.get_downloads_status()

        assert status["active"] is None
        assert len(status["pending"]) == 2
        assert status["pending"][0]["title"] == "Song 1"
        assert status["pending"][1]["title"] == "Song 2"

    def test_get_downloads_status_active(self):
        """Test status with active download."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        # Simulate active download
        dm.active_download = {"title": "Active Song", "progress": 50.0, "status": "downloading"}

        status = dm.get_downloads_status()

        assert status["active"]["title"] == "Active Song"
        assert status["active"]["progress"] == 50.0

    def test_get_downloads_status_errors(self):
        """Test status with download errors."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.download_errors = [
            {
                "id": "1234",
                "title": "Failed Song",
                "url": "http://example.com/fail",
                "user": "User",
                "error": "Error message",
            }
        ]

        status = dm.get_downloads_status()

        assert len(status["errors"]) == 1
        assert status["errors"][0]["title"] == "Failed Song"

    def test_remove_error(self):
        """Test removing an error by ID."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        dm.download_errors = [{"id": "1234", "title": "Failed Song", "error": "Error"}]

        # Test remove invalid ID
        result = dm.remove_error("9999")
        assert result is False
        assert len(dm.download_errors) == 1

        # Test remove valid ID
        result = dm.remove_error("1234")
        assert result is True
        assert len(dm.download_errors) == 0


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
        self, mock_build_cmd, mock_popen, mock_gettext, video_id, file_path
    ):
        """Test enqueue works with special characters in filename."""
        mock_karaoke = MockKaraokeForDownload()
        dm = DownloadManager(mock_karaoke)

        mock_build_cmd.return_value = ["yt-dlp", "url"]
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["Done", ""]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        mock_karaoke.available_songs.find_by_id.return_value = file_path
        mock_karaoke.available_songs.add_if_valid.return_value = True

        dm._execute_download(
            f"https://youtube.com/watch?v={video_id}",
            enqueue=True,
            user="TestUser",
            title="Test",
        )

        mock_karaoke.queue_manager.enqueue.assert_called_once_with(
            file_path, "TestUser", log_action=False
        )
