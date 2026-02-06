"""Unit tests for stream_manager module."""

import subprocess
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.stream_manager import PlaybackResult, StreamManager, enqueue_output


@pytest.fixture
def test_prefs():
    """Create a PreferenceManager for testing."""
    return PreferenceManager("/nonexistent/test_config.ini")


class TestEnqueueOutput:
    """Tests for the enqueue_output function."""

    def test_enqueues_lines_from_stream(self):
        """Test that lines are read from stream and put in queue."""
        mock_stream = MagicMock()
        mock_stream.readline.side_effect = [b"line1\n", b"line2\n", b""]
        queue = Queue()

        enqueue_output(mock_stream, queue)

        assert queue.qsize() == 2
        assert queue.get() == b"line1\n"
        assert queue.get() == b"line2\n"
        mock_stream.close.assert_called_once()

    def test_closes_stream_when_done(self):
        """Test that stream is closed after reading."""
        mock_stream = MagicMock()
        mock_stream.readline.side_effect = [b""]
        queue = Queue()

        enqueue_output(mock_stream, queue)

        mock_stream.close.assert_called_once()


class TestStreamManagerInit:
    """Tests for StreamManager initialization."""

    def test_init_sets_attributes(self, test_prefs):
        """Test that init sets expected attributes."""
        sm = StreamManager(test_prefs)

        assert sm.preferences == test_prefs
        assert sm.ffmpeg_process is None
        assert sm.ffmpeg_log is None


class TestStreamManagerLogFfmpegOutput:
    """Tests for StreamManager.log_ffmpeg_output method."""

    def test_log_output_when_queue_has_items(self, test_prefs):
        """Test logging when queue has output."""
        sm = StreamManager(test_prefs)
        sm.ffmpeg_log = Queue()
        sm.ffmpeg_log.put(b"Processing frame 1\n")
        sm.ffmpeg_log.put(b"Processing frame 2\n")

        with patch("pikaraoke.lib.stream_manager.logging") as mock_logging:
            sm.log_ffmpeg_output()
            assert mock_logging.debug.call_count == 2

    def test_no_log_when_queue_empty(self, test_prefs):
        """Test no logging when queue is empty."""
        sm = StreamManager(test_prefs)
        sm.ffmpeg_log = Queue()

        with patch("pikaraoke.lib.stream_manager.logging") as mock_logging:
            sm.log_ffmpeg_output()
            mock_logging.debug.assert_not_called()

    def test_no_log_when_queue_is_none(self, test_prefs):
        """Test no error when ffmpeg_log is None."""
        sm = StreamManager(test_prefs)
        sm.ffmpeg_log = None

        # Should not raise
        sm.log_ffmpeg_output()


class TestStreamManagerKillFfmpeg:
    """Tests for StreamManager.kill_ffmpeg method."""

    def test_kill_ffmpeg_when_no_process(self, test_prefs):
        """Test kill_ffmpeg does nothing when no process."""
        sm = StreamManager(test_prefs)
        sm.ffmpeg_process = None

        # Should not raise
        sm.kill_ffmpeg()
        assert sm.ffmpeg_process is None

    def test_kill_ffmpeg_graceful_termination(self, test_prefs):
        """Test graceful termination of FFmpeg process."""
        sm = StreamManager(test_prefs)
        mock_process = MagicMock()
        sm.ffmpeg_process = mock_process

        sm.kill_ffmpeg()

        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()
        assert sm.ffmpeg_process is None

    def test_kill_ffmpeg_force_kill_on_timeout(self, test_prefs):
        """Test force kill when graceful termination times out."""
        sm = StreamManager(test_prefs)
        mock_process = MagicMock()
        mock_process.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), None]
        sm.ffmpeg_process = mock_process

        sm.kill_ffmpeg()

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    def test_kill_ffmpeg_handles_exception(self, test_prefs):
        """Test that exceptions during termination are handled."""
        sm = StreamManager(test_prefs)
        mock_process = MagicMock()
        mock_process.terminate.side_effect = Exception("Process error")
        sm.ffmpeg_process = mock_process

        # Should not raise
        sm.kill_ffmpeg()
        assert sm.ffmpeg_process is None


class TestStreamManagerCopyFile:
    """Tests for StreamManager._copy_file method."""

    def test_copy_file_success(self, tmp_path, test_prefs):
        """Test successful file copy."""
        sm = StreamManager(test_prefs)

        src_file = tmp_path / "source.mp4"
        src_file.write_bytes(b"video content")
        dest_file = tmp_path / "dest.mp4"

        result = sm._copy_file(str(src_file), str(dest_file))

        assert result is True
        assert dest_file.exists()
        assert dest_file.read_bytes() == b"video content"


class TestStreamManagerCheckMp4Buffer:
    """Tests for StreamManager._check_mp4_buffer method."""

    def test_returns_false_when_complete_transcode_enabled(self, test_prefs):
        """Test returns False when complete_transcode_before_play is True."""
        test_prefs.set("complete_transcode_before_play", True)
        sm = StreamManager(test_prefs)

        mock_fr = MagicMock()
        mock_fr.output_file = "/tmp/test.mp4"

        result = sm._check_mp4_buffer(mock_fr, 150000)

        assert result is False

    def test_returns_true_when_buffer_full(self, tmp_path, test_prefs):
        """Test returns True when file size exceeds buffer."""
        sm = StreamManager(test_prefs)

        output_file = tmp_path / "output.mp4"
        output_file.write_bytes(b"x" * 200000)

        mock_fr = MagicMock()
        mock_fr.output_file = str(output_file)

        result = sm._check_mp4_buffer(mock_fr, 150000)

        assert result is True

    def test_returns_false_when_buffer_not_full(self, tmp_path, test_prefs):
        """Test returns False when file size is below buffer."""
        sm = StreamManager(test_prefs)

        output_file = tmp_path / "output.mp4"
        output_file.write_bytes(b"x" * 100000)

        mock_fr = MagicMock()
        mock_fr.output_file = str(output_file)

        result = sm._check_mp4_buffer(mock_fr, 150000)

        assert result is False

    def test_returns_false_when_file_not_found(self, test_prefs):
        """Test returns False when output file doesn't exist."""
        sm = StreamManager(test_prefs)

        mock_fr = MagicMock()
        mock_fr.output_file = "/nonexistent/file.mp4"

        result = sm._check_mp4_buffer(mock_fr, 150000)

        assert result is False


class TestStreamManagerCheckHlsBuffer:
    """Tests for StreamManager._check_hls_buffer method."""

    def test_returns_false_when_complete_transcode_enabled(self, test_prefs):
        """Test returns False when complete_transcode_before_play is True."""
        test_prefs.set("complete_transcode_before_play", True)
        sm = StreamManager(test_prefs)

        mock_fr = MagicMock()

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False

    def test_returns_true_when_segments_ready(self, tmp_path, test_prefs):
        """Test returns True when enough segments and buffer size."""
        sm = StreamManager(test_prefs)

        stream_uid = 12345
        # Create segment files
        for i in range(4):
            segment = tmp_path / f"{stream_uid}_segment_{i:03d}.m4s"
            segment.write_bytes(b"x" * 50000)

        # Create the HLS playlist file (output_file)
        playlist_file = tmp_path / f"{stream_uid}.m3u8"
        playlist_file.write_text("#EXTM3U\n#EXT-X-VERSION:7\n")

        mock_fr = MagicMock()
        mock_fr.tmp_dir = str(tmp_path)
        mock_fr.stream_uid = stream_uid
        mock_fr.output_file = str(playlist_file)
        mock_fr.get_current_stream_size.return_value = 200000

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is True

    def test_returns_false_when_not_enough_segments(self, tmp_path, test_prefs):
        """Test returns False when fewer than min segments."""
        sm = StreamManager(test_prefs)

        stream_uid = 12345
        # Create only 2 segments (need 3)
        for i in range(2):
            segment = tmp_path / f"{stream_uid}_segment_{i:03d}.m4s"
            segment.write_bytes(b"x" * 50000)

        mock_fr = MagicMock()
        mock_fr.tmp_dir = str(tmp_path)
        mock_fr.stream_uid = stream_uid

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False

    def test_returns_false_when_tmp_dir_not_found(self, test_prefs):
        """Test returns False when temp directory doesn't exist."""
        sm = StreamManager(test_prefs)

        mock_fr = MagicMock()
        mock_fr.tmp_dir = "/nonexistent/dir"
        mock_fr.stream_uid = 12345

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False


class TestStreamManagerPlayFile:
    """Tests for StreamManager.play_file method."""

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_returns_error_result_on_resolve_error(
        self, mock_resolver_class, mock_gettext, test_prefs
    ):
        """Test play_file returns error PlaybackResult when FileResolver fails."""
        sm = StreamManager(test_prefs)

        mock_resolver_class.side_effect = Exception("File not found")

        result = sm.play_file("/songs/nonexistent.mp4")

        assert isinstance(result, PlaybackResult)
        assert result.success is False
        assert result.error is not None

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_copies_when_no_transcoding_needed(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Test play_file copies file when no transcoding required."""
        sm = StreamManager(test_prefs, streaming_format="mp4")

        mock_fr = MagicMock()
        mock_fr.stream_uid = 12345
        mock_fr.output_file = "/tmp/12345.mp4"
        mock_fr.duration = 180
        mock_fr.ass_file_path = None
        mock_resolver_class.return_value = mock_fr

        with patch.object(sm, "_copy_file", return_value=True) as mock_copy:
            result = sm.play_file("/songs/test.mp4")

        mock_copy.assert_called_once()
        assert isinstance(result, PlaybackResult)
        assert result.success is True
        assert result.duration == 180
