"""Unit tests for download_manager module."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.download_manager import DownloadManager, parse_download_path


class TestParseDownloadPath:
    """Tests for the parse_download_path function."""

    def test_parse_merger_output(self):
        """Test parsing path from Merger output."""
        output = '[Merger] Merging formats into "/home/user/songs/Artist - Song---abc123.mp4"'
        result = parse_download_path(output)
        assert result == "/home/user/songs/Artist - Song---abc123.mp4"

    def test_parse_download_destination(self):
        """Test parsing path from download destination output."""
        output = "[download] Destination: /home/user/songs/Track---xyz789.webm"
        result = parse_download_path(output)
        assert result == "/home/user/songs/Track---xyz789.webm"

    def test_parse_already_downloaded(self):
        """Test parsing path from already downloaded message."""
        output = "[download] /home/user/songs/Song---def456.mp4 has already been downloaded"
        result = parse_download_path(output)
        assert result == "/home/user/songs/Song---def456.mp4"

    def test_parse_multiline_output_with_merger(self):
        """Test parsing from multiline output with Merger at the end."""
        output = """[youtube] abc123: Downloading webpage
[youtube] abc123: Downloading ios player API JSON
[info] abc123: Downloading 1 format(s)
[download] Destination: /tmp/Artist - Song---abc123.f137.mp4
[download] 100% of 50.00MiB
[download] Destination: /tmp/Artist - Song---abc123.f251.webm
[download] 100% of 5.00MiB
[Merger] Merging formats into "/home/user/songs/Artist - Song---abc123.mp4"
Deleting original file /tmp/Artist - Song---abc123.f137.mp4"""
        result = parse_download_path(output)
        assert result == "/home/user/songs/Artist - Song---abc123.mp4"

    def test_parse_multiline_output_destination_only(self):
        """Test parsing from multiline output with only destination."""
        output = """[youtube] xyz789: Downloading webpage
[info] xyz789: Downloading 1 format(s)
[download] Destination: /home/user/songs/Track---xyz789.mp4
[download] 100% of 25.00MiB"""
        result = parse_download_path(output)
        assert result == "/home/user/songs/Track---xyz789.mp4"

    def test_parse_no_match_returns_none(self):
        """Test that unrecognized output returns None."""
        output = "[youtube] abc123: Downloading webpage"
        result = parse_download_path(output)
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """Test that empty string returns None."""
        result = parse_download_path("")
        assert result is None

    def test_parse_path_with_spaces(self):
        """Test parsing path with spaces in filename."""
        output = '[Merger] Merging formats into "/home/user/My Songs/Artist Name - Song Title---abc123.mp4"'
        result = parse_download_path(output)
        assert result == "/home/user/My Songs/Artist Name - Song Title---abc123.mp4"

    def test_parse_windows_style_path(self):
        """Test parsing Windows-style path."""
        output = '[Merger] Merging formats into "C:\\Users\\user\\songs\\Track---abc123.mp4"'
        result = parse_download_path(output)
        assert result == "C:\\Users\\user\\songs\\Track---abc123.mp4"


class MockKaraokeForDownload:
    """Mock Karaoke instance for DownloadManager tests."""

    def __init__(self):
        self.youtubedl_path = "yt-dlp"
        self.download_path = "/songs/"
        self.high_quality = False
        self.youtubedl_proxy = None
        self.additional_ytdl_args = None
        self.available_songs = MagicMock()
        self.log_and_send = MagicMock()
        self.enqueue = MagicMock()


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
        mock_process.stdout.readline.side_effect = [
            '[Merger] Merging formats into "/songs/Artist - Song---abc123.mp4"',
            "",
        ]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        rc = dm._execute_download("https://youtube.com/watch?v=test", False, "User", "Title")

        assert rc == 0
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
        mock_process.stdout.readline.side_effect = [
            '[Merger] Merging formats into "/songs/Song---abc.mp4"',
            "",
        ]
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process

        dm._execute_download("https://youtube.com/watch?v=test", True, "TestUser", "Title")

        mock_karaoke.enqueue.assert_called_once_with(
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

        dm._execute_download("url", True, "User", "Title")

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
