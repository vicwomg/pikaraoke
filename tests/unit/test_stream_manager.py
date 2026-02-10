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

    @patch("pikaraoke.lib.stream_manager.time")
    @patch("pikaraoke.lib.stream_manager.os.path.exists", return_value=False)
    @patch("pikaraoke.lib.stream_manager.shutil")
    def test_copy_file_returns_false_when_dest_never_appears(
        self, mock_shutil, mock_exists, mock_time, test_prefs
    ):
        """Test _copy_file returns False when destination never appears after copy."""
        sm = StreamManager(test_prefs)

        result = sm._copy_file("/src/file.mp4", "/dest/file.mp4")

        assert result is False
        mock_shutil.copy.assert_called_once()


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

    STREAM_UID = 12345

    def _create_segments(self, tmp_path, count=4):
        """Create HLS segment files in tmp_path."""
        for i in range(count):
            segment = tmp_path / f"{self.STREAM_UID}_segment_{i:03d}.m4s"
            segment.write_bytes(b"x" * 50000)

    def _make_mock_fr(self, tmp_path, playlist_content=None):
        """Create a mock FileResolver for HLS buffer tests.

        When playlist_content is provided, a playlist file is created and
        mock_fr.output_file is set to its path.
        """
        mock_fr = MagicMock()
        mock_fr.tmp_dir = str(tmp_path)
        mock_fr.stream_uid = self.STREAM_UID
        if playlist_content is not None:
            playlist_file = tmp_path / f"{self.STREAM_UID}.m3u8"
            playlist_file.write_text(playlist_content)
            mock_fr.output_file = str(playlist_file)
        return mock_fr

    def test_returns_false_when_complete_transcode_enabled(self, test_prefs):
        """Test returns False when complete_transcode_before_play is True."""
        test_prefs.set("complete_transcode_before_play", True)
        sm = StreamManager(test_prefs)

        result = sm._check_hls_buffer(MagicMock(), 150000)

        assert result is False

    def test_returns_true_when_segments_ready(self, tmp_path, test_prefs):
        """Test returns True when enough segments and buffer size."""
        sm = StreamManager(test_prefs)
        self._create_segments(tmp_path)
        mock_fr = self._make_mock_fr(tmp_path, "#EXTM3U\n#EXT-X-VERSION:7\n")
        mock_fr.get_current_stream_size.return_value = 200000

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is True

    def test_returns_false_when_not_enough_segments(self, tmp_path, test_prefs):
        """Test returns False when fewer than min segments."""
        sm = StreamManager(test_prefs)
        self._create_segments(tmp_path, count=2)
        mock_fr = self._make_mock_fr(tmp_path)

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False

    def test_returns_false_when_tmp_dir_not_found(self, test_prefs):
        """Test returns False when temp directory doesn't exist."""
        sm = StreamManager(test_prefs)

        mock_fr = MagicMock()
        mock_fr.tmp_dir = "/nonexistent/dir"
        mock_fr.stream_uid = self.STREAM_UID

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False

    def test_returns_false_when_playlist_empty(self, tmp_path, test_prefs):
        """Test returns False when playlist file exists but is empty."""
        sm = StreamManager(test_prefs)
        mock_fr = self._make_mock_fr(tmp_path, "")

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False

    @pytest.mark.parametrize(
        "error",
        [OSError("Disk error"), RuntimeError("Unexpected")],
        ids=["os_error", "unexpected_error"],
    )
    def test_returns_false_on_stream_size_error(self, tmp_path, test_prefs, error):
        """Test returns False when get_current_stream_size raises an exception."""
        sm = StreamManager(test_prefs)
        self._create_segments(tmp_path)
        mock_fr = self._make_mock_fr(tmp_path, "#EXTM3U\n")
        mock_fr.get_current_stream_size.side_effect = error

        result = sm._check_hls_buffer(mock_fr, 150000)

        assert result is False


class TestStreamManagerTranscodeFile:
    """Tests for StreamManager._transcode_file method."""

    def _make_mock_fr(self):
        """Create a mock FileResolver for transcoding tests."""
        mock_fr = MagicMock()
        mock_fr.stream_uid = 12345
        mock_fr.output_file = "/tmp/12345.mp4"
        mock_fr.duration = 180
        mock_fr.tmp_dir = "/tmp"
        mock_fr.get_current_stream_size.return_value = 500000
        return mock_fr

    def _make_mock_ffmpeg(self, mock_build_cmd, poll_return: int | None = 0):
        """Create mock FFmpeg command and process, wired to build_ffmpeg_cmd."""
        mock_process = MagicMock()
        mock_process.poll.return_value = poll_return
        mock_cmd = MagicMock()
        mock_cmd.run_async.return_value = mock_process
        mock_build_cmd.return_value = mock_cmd
        return mock_cmd, mock_process

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_success(self, mock_build_cmd, mock_thread, test_prefs):
        """Test successful transcoding when FFmpeg exits with code 0."""
        sm = StreamManager(test_prefs)
        mock_cmd, _ = self._make_mock_ffmpeg(mock_build_cmd, poll_return=0)

        is_complete, is_buffered = sm._transcode_file(
            self._make_mock_fr(), semitones=2, is_hls=False
        )

        assert is_complete is True
        mock_build_cmd.assert_called_once()
        mock_cmd.run_async.assert_called_once_with(pipe_stderr=True, pipe_stdin=True)

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_ffmpeg_error(self, mock_build_cmd, mock_thread, test_prefs):
        """Test transcoding failure when FFmpeg exits with non-zero code."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=1)

        is_complete, is_buffered = sm._transcode_file(
            self._make_mock_fr(), semitones=0, is_hls=False
        )

        assert is_complete is False
        assert is_buffered is False

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_buffering_complete_before_finish(
        self, mock_build_cmd, mock_thread, test_prefs
    ):
        """Test that buffering can complete before transcoding finishes."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=None)

        with patch.object(sm, "_check_mp4_buffer", return_value=True):
            is_complete, is_buffered = sm._transcode_file(
                self._make_mock_fr(), semitones=0, is_hls=False
            )

        assert is_complete is False
        assert is_buffered is True

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_hls_buffering(self, mock_build_cmd, mock_thread, test_prefs):
        """Test HLS buffering check is used when is_hls=True."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=None)

        with patch.object(sm, "_check_hls_buffer", return_value=True) as mock_hls:
            is_complete, is_buffered = sm._transcode_file(
                self._make_mock_fr(), semitones=0, is_hls=True
            )

        mock_hls.assert_called()
        assert is_buffered is True

    @patch("pikaraoke.lib.stream_manager.time")
    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_max_retries_exceeded(
        self, mock_build_cmd, mock_thread, mock_time, test_prefs
    ):
        """Test that max retries limit prevents infinite loop."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=None)

        with patch.object(sm, "_check_mp4_buffer", return_value=False):
            is_complete, is_buffered = sm._transcode_file(
                self._make_mock_fr(), semitones=0, is_hls=False
            )

        assert is_complete is False
        assert is_buffered is False

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_kills_existing_ffmpeg(self, mock_build_cmd, mock_thread, test_prefs):
        """Test that _transcode_file kills any existing FFmpeg process first."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=0)

        with patch.object(sm, "kill_ffmpeg") as mock_kill:
            sm._transcode_file(self._make_mock_fr(), semitones=0, is_hls=False)

        mock_kill.assert_called_once()


class TestStreamManagerPlayFile:
    """Tests for StreamManager.play_file method."""

    def _setup_resolver(
        self, mock_resolver_class, output_ext="mp4", duration=200, ass_file_path=None
    ):
        """Configure mock FileResolver with standard play_file test attributes."""
        mock_fr = MagicMock()
        mock_fr.stream_uid = 12345
        mock_fr.output_file = f"/tmp/12345.{output_ext}"
        mock_fr.duration = duration
        mock_fr.ass_file_path = ass_file_path
        mock_resolver_class.return_value = mock_fr
        return mock_fr

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
        self._setup_resolver(mock_resolver_class, duration=180)

        with patch.object(sm, "_copy_file", return_value=True) as mock_copy:
            result = sm.play_file("/songs/test.mp4")

        mock_copy.assert_called_once()
        assert isinstance(result, PlaybackResult)
        assert result.success is True
        assert result.duration == 180

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_hls_stream_url(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Test play_file produces HLS stream URL when format is hls."""
        sm = StreamManager(test_prefs, streaming_format="hls")
        self._setup_resolver(mock_resolver_class, output_ext="m3u8")

        with patch.object(sm, "_transcode_file", return_value=(True, False)):
            result = sm.play_file("/songs/test.mp4")

        assert result.success is True
        assert result.stream_url == "/stream/12345.m3u8"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=True)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_mp4_progressive_stream_url(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Test play_file produces progressive MP4 URL when buffering."""
        sm = StreamManager(test_prefs, streaming_format="mp4")
        self._setup_resolver(mock_resolver_class)

        with patch.object(sm, "_transcode_file", return_value=(False, True)):
            result = sm.play_file("/songs/test.mp4")

        assert result.success is True
        assert result.stream_url == "/stream/12345.mp4"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=True)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_mp4_full_transcode_url(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Test play_file produces full transcode URL when setting enabled."""
        test_prefs.set("complete_transcode_before_play", True)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        self._setup_resolver(mock_resolver_class)

        with patch.object(sm, "_transcode_file", return_value=(True, False)):
            result = sm.play_file("/songs/test.mp4")

        assert result.success is True
        assert result.stream_url == "/stream/full/12345"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_includes_subtitle_url(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Test play_file includes subtitle URL when subtitle file exists."""
        sm = StreamManager(test_prefs, streaming_format="mp4")
        self._setup_resolver(mock_resolver_class, duration=180, ass_file_path="/tmp/12345.ass")

        with patch.object(sm, "_copy_file", return_value=True):
            result = sm.play_file("/songs/test.mp4")

        assert result.success is True
        assert result.subtitle_url == "/subtitle/12345"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=True)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_returns_failure_when_stream_not_ready(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Test play_file returns failure when neither transcoding nor buffering completes."""
        sm = StreamManager(test_prefs, streaming_format="mp4")
        self._setup_resolver(mock_resolver_class)

        with patch.object(sm, "_transcode_file", return_value=(False, False)):
            result = sm.play_file("/songs/test.mp4")

        assert result.success is False
        assert result.error is not None
