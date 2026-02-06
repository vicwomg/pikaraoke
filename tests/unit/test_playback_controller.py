"""Unit tests for playback_controller module."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.playback_controller import PlaybackController, PlaybackResult
from pikaraoke.lib.preference_manager import PreferenceManager


@pytest.fixture
def test_prefs():
    """Create a PreferenceManager for testing."""
    return PreferenceManager("/nonexistent/test_config.ini")


class TestPlaybackResult:
    """Tests for PlaybackResult dataclass."""

    def test_success_result(self):
        """Test successful playback result."""
        result = PlaybackResult(
            success=True,
            stream_url="/stream/123.m3u8",
            subtitle_url="/subtitle/123",
            duration=180,
        )

        assert result.success is True
        assert result.stream_url == "/stream/123.m3u8"
        assert result.subtitle_url == "/subtitle/123"
        assert result.duration == 180
        assert result.error is None

    def test_failure_result(self):
        """Test failed playback result."""
        result = PlaybackResult(success=False, error="File not found")

        assert result.success is False
        assert result.error == "File not found"
        assert result.stream_url is None
        assert result.subtitle_url is None
        assert result.duration is None


class TestPlaybackControllerInit:
    """Tests for PlaybackController initialization."""

    def test_init_sets_attributes(self, test_prefs):
        """Test that init sets expected attributes."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)

        assert pc.preferences == test_prefs
        assert pc.events == events
        assert pc.filename_from_path == filename_fn
        assert pc.now_playing is None
        assert pc.now_playing_filename is None
        assert pc.now_playing_user is None
        assert pc.is_paused is True
        assert pc.is_playing is False


class TestPlaybackControllerPlayFile:
    """Tests for PlaybackController.play_file method."""

    @patch("pikaraoke.lib.playback_controller.time.sleep")
    def test_play_file_success(self, mock_sleep, test_prefs):
        """Test successful playback."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: "Test Song"

        pc = PlaybackController(test_prefs, events, filename_fn)

        # Mock StreamManager.play_file to return success
        mock_result = PlaybackResult(
            success=True,
            stream_url="/stream/123.m3u8",
            subtitle_url=None,
            duration=180,
        )
        pc.stream_manager.play_file = MagicMock(return_value=mock_result)

        # Simulate client connecting
        pc.is_playing = True

        result = pc.play_file("/songs/test.mp4", "TestUser", semitones=2)

        assert result.success is True
        assert pc.now_playing == "Test Song"
        assert pc.now_playing_filename == "/songs/test.mp4"
        assert pc.now_playing_user == "TestUser"
        assert pc.now_playing_transpose == 2
        assert pc.now_playing_duration == 180
        assert pc.is_paused is False

    @patch("pikaraoke.lib.playback_controller.time.sleep")
    @patch("flask_babel._", side_effect=lambda x: x)
    def test_play_file_timeout(self, mock_gettext, mock_sleep, test_prefs):
        """Test playback timeout when client never connects."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: "Test Song"

        pc = PlaybackController(test_prefs, events, filename_fn)

        # Mock StreamManager.play_file to return success
        mock_result = PlaybackResult(
            success=True,
            stream_url="/stream/123.m3u8",
            subtitle_url=None,
            duration=180,
        )
        pc.stream_manager.play_file = MagicMock(return_value=mock_result)
        pc.stream_manager.kill_ffmpeg = MagicMock()

        # Client never connects (is_playing stays False)
        result = pc.play_file("/songs/test.mp4", "TestUser", semitones=0)

        assert result.success is False
        assert result.error is not None

    def test_play_file_stream_failure(self, test_prefs):
        """Test playback when stream setup fails."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: "Test Song"

        pc = PlaybackController(test_prefs, events, filename_fn)

        # Mock StreamManager.play_file to return failure
        mock_result = PlaybackResult(success=False, error="File not found")
        pc.stream_manager.play_file = MagicMock(return_value=mock_result)

        result = pc.play_file("/songs/nonexistent.mp4", "TestUser")

        assert result.success is False
        assert result.error == "File not found"


class TestPlaybackControllerStartSong:
    """Tests for PlaybackController.start_song method."""

    def test_start_song_sets_playing(self, test_prefs):
        """Test that start_song sets is_playing to True."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)
        pc.now_playing = "Test Song"

        pc.start_song()

        assert pc.is_playing is True


class TestPlaybackControllerEndSong:
    """Tests for PlaybackController.end_song method."""

    @patch("pikaraoke.lib.playback_controller.time.sleep")
    @patch("pikaraoke.lib.playback_controller.delete_tmp_dir")
    def test_end_song_cleans_up(self, mock_delete, mock_sleep, test_prefs):
        """Test that end_song cleans up resources."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)
        pc.now_playing = "Test Song"
        pc.is_playing = True
        pc.stream_manager.kill_ffmpeg = MagicMock()

        # Track emitted events
        emitted_events = []
        events.on("song_ended", lambda: emitted_events.append("song_ended"))

        pc.end_song()

        assert pc.is_playing is False
        assert pc.now_playing is None
        pc.stream_manager.kill_ffmpeg.assert_called_once()
        mock_delete.assert_called_once()
        mock_sleep.assert_called_once()
        assert "song_ended" in emitted_events


class TestPlaybackControllerSkip:
    """Tests for PlaybackController.skip method."""

    @patch("pikaraoke.lib.playback_controller.time.sleep")
    @patch("pikaraoke.lib.playback_controller.delete_tmp_dir")
    @patch("flask_babel._", side_effect=lambda x: x)
    def test_skip_when_playing(self, mock_gettext, mock_delete, mock_sleep, test_prefs):
        """Test skip when a song is playing."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)
        pc.now_playing = "Test Song"
        pc.is_playing = True
        pc.stream_manager.kill_ffmpeg = MagicMock()

        result = pc.skip(log_action=True)

        assert result is True
        assert pc.is_playing is False

    def test_skip_when_not_playing(self, test_prefs):
        """Test skip when nothing is playing."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)

        result = pc.skip()

        assert result is False


class TestPlaybackControllerPause:
    """Tests for PlaybackController.pause method."""

    @patch("flask_babel._", side_effect=lambda x: x)
    def test_pause_when_playing(self, mock_gettext, test_prefs):
        """Test pause toggles pause state."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)
        pc.is_playing = True
        pc.is_paused = False

        result = pc.pause()

        assert result is True
        assert pc.is_paused is True

        # Pause again (unpause)
        result = pc.pause()

        assert result is True
        assert pc.is_paused is False

    def test_pause_when_not_playing(self, test_prefs):
        """Test pause when nothing is playing."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)

        result = pc.pause()

        assert result is False


class TestPlaybackControllerGetNowPlaying:
    """Tests for PlaybackController.get_now_playing method."""

    def test_get_now_playing_returns_state(self, test_prefs):
        """Test that get_now_playing returns current state."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)
        pc.now_playing = "Test Song"
        pc.now_playing_user = "TestUser"
        pc.now_playing_transpose = 2
        pc.is_paused = False

        state = pc.get_now_playing()

        assert state["now_playing"] == "Test Song"
        assert state["now_playing_user"] == "TestUser"
        assert state["now_playing_transpose"] == 2
        assert state["is_paused"] is False


class TestPlaybackControllerResetNowPlaying:
    """Tests for PlaybackController.reset_now_playing method."""

    def test_reset_clears_all_state(self, test_prefs):
        """Test that reset clears all now playing state."""
        events = EventSystem()
        filename_fn = lambda x, remove_youtube_id=True: x

        pc = PlaybackController(test_prefs, events, filename_fn)
        pc.now_playing = "Test Song"
        pc.now_playing_user = "TestUser"
        pc.is_playing = True
        pc.is_paused = False

        pc.reset_now_playing()

        assert pc.now_playing is None
        assert pc.now_playing_user is None
        assert pc.is_playing is False
        assert pc.is_paused is True
