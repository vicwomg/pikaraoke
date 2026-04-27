"""Unit tests for stream_manager module."""

import subprocess
import threading
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.stream_manager import (
    PlaybackResult,
    StreamManager,
    _parse_ffmpeg_time_seconds,
    enqueue_output,
)


@pytest.fixture
def test_prefs():
    """PreferenceManager with Demucs off so tests don't depend on host GPU."""
    prefs = PreferenceManager("/nonexistent/test_config.ini")
    prefs.DEFAULTS = {**PreferenceManager.DEFAULTS, "vocal_removal": False}
    return prefs


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

    def test_on_line_callback_runs_per_line(self):
        """US-23: MP4 stderr tap invokes the callback for each line."""
        mock_stream = MagicMock()
        mock_stream.readline.side_effect = [b"a\n", b"b\n", b""]
        queue = Queue()
        seen = []

        enqueue_output(mock_stream, queue, on_line=seen.append)

        assert seen == [b"a\n", b"b\n"]
        # Callback does not replace queueing.
        assert queue.qsize() == 2

    def test_on_line_callback_exception_does_not_break_reader(self):
        """A throwing callback must not stop the queue from filling."""
        mock_stream = MagicMock()
        mock_stream.readline.side_effect = [b"a\n", b""]
        queue = Queue()

        def _boom(_line):
            raise RuntimeError("cb crashed")

        enqueue_output(mock_stream, queue, on_line=_boom)

        assert queue.get() == b"a\n"


class TestParseFfmpegTimeSeconds:
    """US-23: time= parser for the MP4 stderr tap."""

    def test_parses_hms_with_hundredths(self):
        line = b"frame=100 fps=30 size=1024kB time=00:01:02.34 bitrate=1000k speed=1.0x"
        assert _parse_ffmpeg_time_seconds(line) == pytest.approx(62.34)

    def test_parses_hms_without_fraction(self):
        line = b"time=01:00:00 bitrate=..."
        assert _parse_ffmpeg_time_seconds(line) == 3600.0

    def test_none_when_no_time_field(self):
        assert _parse_ffmpeg_time_seconds(b"frame=100 fps=30") is None


class TestStreamManagerInit:
    """Tests for StreamManager initialization."""

    def test_init_sets_attributes(self, test_prefs):
        """Test that init sets expected attributes."""
        sm = StreamManager(test_prefs)

        assert sm.preferences == test_prefs
        assert sm.ffmpeg_process is None
        assert sm.ffmpeg_log is None


class TestStemsLifecycle:
    """Play -> end -> replay lifecycle with stem WAV/MP3 cache transitions.

    After first-play ends, ``clear_active_stems`` must drop the WAVs that
    are no longer needed (MP3 siblings ready) but not before. On replay
    ``_register_cached_stems`` must emit MP3 stem URLs — the stale WAV
    URLs from the previous play would 404/416 because the files are gone.
    """

    def _capture_events(self, events: EventSystem, name: str) -> list:
        captured: list = []
        events.on(name, lambda payload: captured.append(payload))
        return captured

    def test_register_cached_mp3_emits_mp3_urls(self, tmp_path, test_prefs):
        cache_key = "a" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        vocals_mp3 = cache_dir / "vocals.mp3"
        instrumental_mp3 = cache_dir / "instrumental.mp3"
        vocals_mp3.write_bytes(b"mp3_v")
        instrumental_mp3.write_bytes(b"mp3_i")

        events = EventSystem()
        captured = self._capture_events(events, "stems_ready")
        sm = StreamManager(test_prefs, events=events)

        sm._register_cached_stems(
            "uid_replay",
            cache_key,
            (str(vocals_mp3), str(instrumental_mp3), "mp3"),
            total_seconds=180.0,
        )

        entry = sm.active_stems["uid_replay"]
        assert entry.format == "mp3"
        assert entry.vocals_path == str(vocals_mp3)
        assert entry.instrumental_path == str(instrumental_mp3)

        assert len(captured) == 1
        payload = captured[0]
        assert payload["vocals_url"] == "/stream/uid_replay/vocals.mp3"
        assert payload["instrumental_url"] == "/stream/uid_replay/instrumental.mp3"

    def test_register_cached_mp3_does_not_kick_encode(self, tmp_path, test_prefs):
        """MP3 cache hit means encoding is already done — don't re-invoke it."""
        cache_key = "b" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        vocals_mp3 = cache_dir / "vocals.mp3"
        instrumental_mp3 = cache_dir / "instrumental.mp3"
        vocals_mp3.write_bytes(b"mp3_v")
        instrumental_mp3.write_bytes(b"mp3_i")

        sm = StreamManager(test_prefs, events=EventSystem())

        with patch("pikaraoke.lib.demucs_processor.encode_mp3_in_background") as mock_encode:
            sm._register_cached_stems(
                "uid",
                cache_key,
                (str(vocals_mp3), str(instrumental_mp3), "mp3"),
                total_seconds=180.0,
            )

        mock_encode.assert_not_called()

    def test_clear_active_stems_cleans_wavs_when_mp3s_ready(self, tmp_path, test_prefs):
        """End-of-song path: WAVs go away but only once both MP3 siblings
        are on disk. Otherwise (e.g. MP3 encode still in flight) the WAV
        stems stay so a refreshed play with WAV cache can keep working.
        """
        cache_key = "c" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        (cache_dir / "vocals.mp3").write_bytes(b"mp3_v")
        (cache_dir / "instrumental.mp3").write_bytes(b"mp3_i")

        sm = StreamManager(test_prefs, events=EventSystem())
        from pikaraoke.lib import demucs_processor as dp

        with patch.object(dp, "CACHE_DIR", str(tmp_path)):
            sm._register_cached_stems(
                "uid",
                cache_key,
                (str(cache_dir / "vocals.wav"), str(cache_dir / "instrumental.wav"), "wav"),
                total_seconds=180.0,
            )
            # Avoid a real ffmpeg spawn when _register_cached_stems calls
            # encode_mp3_in_background for the WAV format branch.
            # (MP3s already exist so the function early-exits on noop.)
            sm.clear_active_stems()

        assert sm.active_stems == {}
        assert not (cache_dir / "vocals.wav").exists()
        assert not (cache_dir / "instrumental.wav").exists()
        assert (cache_dir / "vocals.mp3").exists()
        assert (cache_dir / "instrumental.mp3").exists()

    def test_clear_active_stems_keeps_wavs_when_mp3s_missing(self, tmp_path, test_prefs):
        """MP3 encode didn't finish yet — WAVs must survive so the next play
        from this same cache_key still has stems to serve.
        """
        cache_key = "d" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")

        sm = StreamManager(test_prefs, events=EventSystem())
        from pikaraoke.lib import demucs_processor as dp

        with patch.object(dp, "CACHE_DIR", str(tmp_path)):
            sm._register_cached_stems(
                "uid",
                cache_key,
                (str(cache_dir / "vocals.wav"), str(cache_dir / "instrumental.wav"), "wav"),
                total_seconds=180.0,
            )
            sm.clear_active_stems()

        assert (cache_dir / "vocals.wav").exists()
        assert (cache_dir / "instrumental.wav").exists()

    def test_full_play_end_replay_cycle(self, tmp_path, test_prefs):
        """The full symptom the user reported: first-play WAV stems,
        song ends (WAVs deleted because MP3s present), replay registers
        stems from MP3 cache and emits .mp3 URLs — not stale .wav URLs.
        """
        cache_key = "e" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        # Cold cache -> Demucs runs -> WAVs written
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        # Background MP3 encode finished during song
        (cache_dir / "vocals.mp3").write_bytes(b"mp3_v")
        (cache_dir / "instrumental.mp3").write_bytes(b"mp3_i")

        events = EventSystem()
        captured = self._capture_events(events, "stems_ready")
        sm = StreamManager(test_prefs, events=events)
        from pikaraoke.lib import demucs_processor as dp

        with patch.object(dp, "CACHE_DIR", str(tmp_path)):
            # First play: WAV cache-hit path
            sm._register_cached_stems(
                "uid_first",
                cache_key,
                (str(cache_dir / "vocals.wav"), str(cache_dir / "instrumental.wav"), "wav"),
                total_seconds=180.0,
            )
            # Song ends
            sm.clear_active_stems()

            # Replay — MP3 cache-hit path (get_cached_stems prefers MP3)
            sm._register_cached_stems(
                "uid_replay",
                cache_key,
                (str(cache_dir / "vocals.mp3"), str(cache_dir / "instrumental.mp3"), "mp3"),
                total_seconds=180.0,
            )

        assert not (cache_dir / "vocals.wav").exists()
        assert len(captured) == 2
        first = captured[0]
        second = captured[1]
        assert first["vocals_url"].endswith("/vocals.wav")
        assert second["vocals_url"].endswith("/vocals.mp3")
        assert second["instrumental_url"].endswith("/instrumental.mp3")
        assert sm.active_stems["uid_replay"].format == "mp3"


class TestIsCacheKeyActive:
    """``is_cache_key_active`` powers the WAV-deletion gate in the prewarm
    encoder; the boundary contract is what the encoder relies on, so test it
    directly rather than only through the integration path."""

    def _register(self, sm, stream_uid, cache_key, vocals_path):
        from pikaraoke.lib.stream_manager import ActiveStems

        done = threading.Event()
        done.set()
        ready = threading.Event()
        ready.set()
        sm.active_stems[stream_uid] = ActiveStems(
            vocals_path=vocals_path,
            instrumental_path=vocals_path,
            format="wav",
            done_event=done,
            ready_event=ready,
            cache_key=cache_key,
        )

    def test_returns_true_for_registered_cache_key(self, test_prefs):
        sm = StreamManager(test_prefs, events=EventSystem())
        self._register(sm, "uid_a", "k" * 64, "/tmp/vocals.wav")
        assert sm.is_cache_key_active("k" * 64) is True

    def test_returns_false_for_unknown_cache_key(self, test_prefs):
        sm = StreamManager(test_prefs, events=EventSystem())
        self._register(sm, "uid_a", "k" * 64, "/tmp/vocals.wav")
        assert sm.is_cache_key_active("z" * 64) is False

    def test_returns_false_when_no_streams(self, test_prefs):
        sm = StreamManager(test_prefs, events=EventSystem())
        assert sm.is_cache_key_active("k" * 64) is False

    def test_clear_drops_active_state(self, test_prefs, tmp_path):
        from pikaraoke.lib import demucs_processor as dp

        sm = StreamManager(test_prefs, events=EventSystem())
        cache_key = "k" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        # Don't seed mp3s -> clear_active_stems' cleanup helper is a no-op.
        self._register(sm, "uid_a", cache_key, str(cache_dir / "vocals.wav"))
        with patch.object(dp, "CACHE_DIR", str(tmp_path)):
            sm.clear_active_stems()
        assert sm.is_cache_key_active(cache_key) is False


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
        """With ffmpeg_log=None: no logging, no exception, state unchanged."""
        sm = StreamManager(test_prefs)
        sm.ffmpeg_log = None

        with patch("pikaraoke.lib.stream_manager.logging") as mock_logging:
            sm.log_ffmpeg_output()
            mock_logging.debug.assert_not_called()
            mock_logging.warning.assert_not_called()
            mock_logging.error.assert_not_called()
        assert sm.ffmpeg_log is None


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
        """When terminate() raises, kill() and wait() are skipped but state is still reset."""
        sm = StreamManager(test_prefs)
        mock_process = MagicMock()
        mock_process.terminate.side_effect = Exception("Process error")
        sm.ffmpeg_process = mock_process

        sm.kill_ffmpeg()  # must not raise

        # Exception path: neither SIGKILL nor the wait() loop ran
        mock_process.kill.assert_not_called()
        mock_process.wait.assert_not_called()
        # finally-block still resets state
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

    def _make_mock_fr(self, audio_sibling_path=None):
        """Create a mock FileResolver for transcoding tests."""
        mock_fr = MagicMock()
        mock_fr.stream_uid = 12345
        mock_fr.output_file = "/tmp/12345.mp4"
        mock_fr.duration = 180
        mock_fr.tmp_dir = "/tmp"
        mock_fr.audio_sibling_path = audio_sibling_path
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
        # FFmpeg completed before buffering check ever ran
        assert is_buffered is False
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
        fr = self._make_mock_fr()
        expected_buffer_size = int(test_prefs.get_or_default("buffer_size")) * 1000

        with patch.object(sm, "_check_hls_buffer", return_value=True) as mock_hls:
            is_complete, is_buffered = sm._transcode_file(fr, semitones=0, is_hls=True)

        mock_hls.assert_called_once_with(fr, expected_buffer_size)
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

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_routes_sibling_audio_when_silent_mp4(
        self, mock_build_cmd, mock_thread, test_prefs
    ):
        """Silent mp4 + .m4a sibling: pass sibling as alternate_audio so the
        muxed ffmpeg map doesn't fail on a missing audio stream."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=0)
        fr = self._make_mock_fr(audio_sibling_path="/songs/test.m4a")

        sm._transcode_file(fr, semitones=0, is_hls=True)

        assert mock_build_cmd.call_args.kwargs["alternate_audio"] == "/songs/test.m4a"

    @patch("pikaraoke.lib.stream_manager.Thread")
    @patch("pikaraoke.lib.stream_manager.build_ffmpeg_cmd")
    def test_transcode_no_sibling_leaves_alternate_audio_none(
        self, mock_build_cmd, mock_thread, test_prefs
    ):
        """Muxed mp4 (no sibling): alternate_audio stays None so the
        original audio track is used."""
        sm = StreamManager(test_prefs)
        self._make_mock_ffmpeg(mock_build_cmd, poll_return=0)

        sm._transcode_file(self._make_mock_fr(), semitones=0, is_hls=True)

        assert mock_build_cmd.call_args.kwargs["alternate_audio"] is None


class TestStreamManagerPlayFile:
    """Tests for StreamManager.play_file method."""

    def _setup_resolver(
        self,
        mock_resolver_class,
        output_ext="mp4",
        duration=200,
        ass_file_path=None,
        audio_sibling_path=None,
    ):
        """Configure mock FileResolver with standard play_file test attributes."""
        mock_fr = MagicMock()
        mock_fr.stream_uid = 12345
        mock_fr.output_file = f"/tmp/12345.{output_ext}"
        mock_fr.duration = duration
        mock_fr.ass_file_path = ass_file_path
        # Plain string so can_serve_* predicates don't blow up on the Mock.
        mock_fr.file_path = "/songs/test.mp4"
        # Default to None so MagicMock's auto-truthy attribute doesn't
        # spuriously trigger the silent-video audio-pipe branch.
        mock_fr.audio_sibling_path = audio_sibling_path
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
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=False)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=False)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_copies_when_no_transcoding_needed(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
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
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_direct_mp4_serves_source(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Vanilla h264/aac mp4 is served directly without copy or transcode."""
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class, duration=180)
        mock_fr.file_path = "/songs/test.mp4"

        with (
            patch.object(sm, "_copy_file") as mock_copy,
            patch.object(sm, "_transcode_file") as mock_transcode,
        ):
            result = sm.play_file("/songs/test.mp4")

        mock_copy.assert_not_called()
        mock_transcode.assert_not_called()
        assert result.success is True
        assert result.stream_url == "/stream/video/12345.mp4"
        assert result.audio_track_url is None
        assert sm.active_sources["12345"] == "/songs/test.mp4"
        assert "12345" not in sm.active_audio

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_direct_video_plus_audio_pipe(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Pitch shift on native mp4 keeps video direct but adds audio pipe route."""
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class, duration=180)
        mock_fr.file_path = "/songs/test.mp4"

        with (
            patch.object(sm, "_copy_file") as mock_copy,
            patch.object(sm, "_transcode_file") as mock_transcode,
        ):
            result = sm.play_file("/songs/test.mp4", semitones=2)

        mock_copy.assert_not_called()
        mock_transcode.assert_not_called()
        assert result.stream_url == "/stream/video/12345.mp4"
        assert result.audio_track_url == "/stream/audio/12345/track.wav"
        assert sm.active_audio["12345"].semitones == 2
        assert sm.active_audio["12345"].source_path == "/songs/test.mp4"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_vocal_removal_suppresses_audio_pipe(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Old muxed mp4 (no sibling): stems eventually carry audio, no warmup pipe."""
        test_prefs.set("vocal_removal", True)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class)  # audio_sibling_path=None
        mock_fr.file_path = "/songs/test.mp4"

        with (
            patch.object(sm, "_prepare_stems") as mock_prep,
            patch.object(sm, "_copy_file") as mock_copy,
            patch.object(sm, "_transcode_file") as mock_transcode,
        ):
            result = sm.play_file("/songs/test.mp4", semitones=2)

        mock_prep.assert_called_once_with(mock_fr)
        mock_copy.assert_not_called()
        mock_transcode.assert_not_called()
        assert result.stream_url == "/stream/video/12345.mp4"
        assert result.audio_track_url is None
        assert "12345" not in sm.active_audio

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_vocal_removal_warmup_pipes_sibling(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Split-download mp4 + stems not cached: m4a sibling piped as warmup audio."""
        import threading

        from pikaraoke.lib.stream_manager import ActiveStems

        test_prefs.set("vocal_removal", True)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class, audio_sibling_path="/songs/test.m4a")
        mock_fr.file_path = "/songs/test.mp4"

        def fake_prepare_stems(fr):
            # Live Demucs: entry exists but done_event is NOT set.
            sm.active_stems[str(fr.stream_uid)] = ActiveStems(
                vocals_path="/cache/vocals.wav.partial",
                instrumental_path="/cache/instrumental.wav.partial",
                format="wav",
                done_event=threading.Event(),
                ready_event=threading.Event(),
            )
            return True

        with patch.object(sm, "_prepare_stems", side_effect=fake_prepare_stems):
            result = sm.play_file("/songs/test.mp4", semitones=2)

        assert result.audio_track_url == "/stream/audio/12345/track.wav"
        assert sm.active_audio["12345"].source_path == "/songs/test.m4a"
        # Transforms still apply to the warmup pipe so the m4a matches the
        # pitch/normalize the stems will be played with.
        assert sm.active_audio["12345"].semitones == 2

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_vocal_removal_cache_hit_skips_warmup(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Cache hit → done_event already set → no warmup pipe needed."""
        import threading

        from pikaraoke.lib.stream_manager import ActiveStems

        test_prefs.set("vocal_removal", True)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class, audio_sibling_path="/songs/test.m4a")
        mock_fr.file_path = "/songs/test.mp4"

        def fake_prepare_stems(fr):
            done = threading.Event()
            done.set()  # cache hit
            ready = threading.Event()
            ready.set()
            sm.active_stems[str(fr.stream_uid)] = ActiveStems(
                vocals_path="/cache/vocals.wav",
                instrumental_path="/cache/instrumental.wav",
                format="wav",
                done_event=done,
                ready_event=ready,
            )
            return True

        with patch.object(sm, "_prepare_stems", side_effect=fake_prepare_stems):
            result = sm.play_file("/songs/test.mp4")

        assert result.audio_track_url is None
        assert "12345" not in sm.active_audio

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_vocal_removal_stashes_transform_prefs(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Transform prefs are stashed on ActiveStems so the stem route can pipe them."""
        import threading

        from pikaraoke.lib.stream_manager import ActiveStems

        test_prefs.set("vocal_removal", True)
        test_prefs.set("normalize_audio", True)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class)
        mock_fr.file_path = "/songs/test.mp4"

        def fake_prepare_stems(fr):
            sm.active_stems[str(fr.stream_uid)] = ActiveStems(
                vocals_path="/cache/vocals.wav",
                instrumental_path="/cache/instrumental.wav",
                format="wav",
                done_event=threading.Event(),
                ready_event=threading.Event(),
            )
            return True

        with patch.object(sm, "_prepare_stems", side_effect=fake_prepare_stems):
            sm.play_file("/songs/test.mp4", semitones=2)

        entry = sm.active_stems["12345"]
        assert entry.semitones == 2
        assert entry.normalize is True

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=True)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_avsync_goes_to_client_on_direct_path(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """avsync is forwarded to the client rather than folded into ffmpeg."""
        test_prefs.set("avsync", 0.25)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class, duration=180)
        mock_fr.file_path = "/songs/test.mp4"

        with (
            patch.object(sm, "_copy_file") as mock_copy,
            patch.object(sm, "_transcode_file") as mock_transcode,
        ):
            result = sm.play_file("/songs/test.mp4")

        mock_copy.assert_not_called()
        mock_transcode.assert_not_called()
        assert result.avsync_offset_ms == 250

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
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_subtitle_url_set_even_without_ass(
        self, mock_resolver_class, mock_transcode_check, mock_gettext, test_prefs
    ):
        """Subtitle URL is published even when .ass hasn't landed yet.

        The lyrics pipeline is async — a song can start playing before any
        .ass is written. _on_lyrics_upgraded needs a base URL to cache-bust
        when the .ass lands mid-playback; leaving subtitle_url=None would
        strand the client without subtitles even after lyrics arrive.
        """
        sm = StreamManager(test_prefs, streaming_format="mp4")
        self._setup_resolver(mock_resolver_class, duration=180, ass_file_path=None)

        with patch.object(sm, "_copy_file", return_value=True):
            result = sm.play_file("/songs/test.mp4")

        assert result.success is True
        assert result.subtitle_url == "/subtitle/12345"

    @patch("flask_babel._", side_effect=lambda x: x)
    @patch("pikaraoke.lib.stream_manager.can_serve_directly", return_value=False)
    @patch("pikaraoke.lib.stream_manager.can_serve_video_directly", return_value=False)
    @patch("pikaraoke.lib.stream_manager.is_transcoding_required", return_value=False)
    @patch("pikaraoke.lib.stream_manager.FileResolver")
    def test_play_file_vocal_removal_only_skips_transcode(
        self,
        mock_resolver_class,
        mock_transcode_check,
        mock_can_video,
        mock_can_direct,
        mock_gettext,
        test_prefs,
    ):
        """Vocal removal alone runs Demucs but does not transcode a vanilla mp4."""
        test_prefs.set("vocal_removal", True)
        sm = StreamManager(test_prefs, streaming_format="mp4")
        mock_fr = self._setup_resolver(mock_resolver_class)

        with (
            patch.object(sm, "_prepare_stems") as mock_prep,
            patch.object(sm, "_copy_file", return_value=True) as mock_copy,
            patch.object(sm, "_transcode_file") as mock_transcode,
        ):
            result = sm.play_file("/songs/test.mp4")

        mock_prep.assert_called_once_with(mock_fr)
        mock_copy.assert_called_once()
        mock_transcode.assert_not_called()
        assert result.success is True

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
