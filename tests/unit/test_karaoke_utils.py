"""Unit tests for Karaoke utility methods."""

import pytest


class TestFilenameFromPath:
    """Tests for the filename_from_path method."""

    def test_filename_from_path_basic(self, mock_karaoke):
        """Test basic filename extraction."""
        result = mock_karaoke.filename_from_path("/songs/My Song.mp4")
        assert result == "My Song"

    def test_filename_from_path_with_youtube_id(self, mock_karaoke):
        """Test that YouTube ID is removed by default."""
        result = mock_karaoke.filename_from_path("/songs/Artist - Song Title---dQw4w9WgXcQ.mp4")
        assert result == "Artist - Song Title"

    def test_filename_from_path_keep_youtube_id(self, mock_karaoke):
        """Test keeping YouTube ID when requested."""
        result = mock_karaoke.filename_from_path(
            "/songs/Artist - Song---dQw4w9WgXcQ.mp4", remove_youtube_id=False
        )
        assert result == "Artist - Song---dQw4w9WgXcQ"

    def test_filename_from_path_nested_directory(self, mock_karaoke):
        """Test with deeply nested path."""
        result = mock_karaoke.filename_from_path(
            "/home/user/music/karaoke/songs/Track---abc123.mp4"
        )
        assert result == "Track"

    def test_filename_from_path_multiple_dashes(self, mock_karaoke):
        """Test filename with multiple dash separators."""
        result = mock_karaoke.filename_from_path("/songs/Artist - Song - Live Version---xyz789.mp4")
        assert result == "Artist - Song - Live Version"

    def test_filename_from_path_no_extension(self, mock_karaoke):
        """Test path without file extension."""
        result = mock_karaoke.filename_from_path("/songs/SongName")
        assert result == "SongName"

    def test_filename_from_path_cdg_zip(self, mock_karaoke):
        """Test with CDG zip file."""
        result = mock_karaoke.filename_from_path("/songs/Karaoke Track---abc.zip")
        assert result == "Karaoke Track"


class TestConvertPreferenceValue:
    """Tests for the _convert_preference_value method."""

    def test_convert_true_values(self, mock_karaoke):
        """Test conversion of truthy string values."""
        assert mock_karaoke._convert_preference_value("true") is True
        assert mock_karaoke._convert_preference_value("True") is True
        assert mock_karaoke._convert_preference_value("TRUE") is True
        assert mock_karaoke._convert_preference_value("yes") is True
        assert mock_karaoke._convert_preference_value("on") is True

    def test_convert_false_values(self, mock_karaoke):
        """Test conversion of falsy string values."""
        assert mock_karaoke._convert_preference_value("false") is False
        assert mock_karaoke._convert_preference_value("False") is False
        assert mock_karaoke._convert_preference_value("FALSE") is False
        assert mock_karaoke._convert_preference_value("no") is False
        assert mock_karaoke._convert_preference_value("off") is False

    def test_convert_integer_values(self, mock_karaoke):
        """Test conversion of integer string values."""
        assert mock_karaoke._convert_preference_value("42") == 42
        assert mock_karaoke._convert_preference_value("0") == 0
        assert mock_karaoke._convert_preference_value("-5") == -5

    def test_convert_float_values(self, mock_karaoke):
        """Test conversion of float string values."""
        assert mock_karaoke._convert_preference_value("3.14") == 3.14
        assert mock_karaoke._convert_preference_value("0.5") == 0.5
        assert mock_karaoke._convert_preference_value("-2.5") == -2.5

    def test_convert_string_passthrough(self, mock_karaoke):
        """Test that non-special strings pass through unchanged."""
        assert mock_karaoke._convert_preference_value("hello") == "hello"
        assert mock_karaoke._convert_preference_value("some text") == "some text"
        assert mock_karaoke._convert_preference_value("/path/to/file") == "/path/to/file"

    def test_convert_non_string_passthrough(self, mock_karaoke):
        """Test that non-string values pass through unchanged."""
        assert mock_karaoke._convert_preference_value(42) == 42
        assert mock_karaoke._convert_preference_value(3.14) == 3.14
        assert mock_karaoke._convert_preference_value(True) is True
        assert mock_karaoke._convert_preference_value(None) is None


class TestGetNowPlaying:
    """Tests for the get_now_playing method."""

    def test_get_now_playing_empty(self, mock_karaoke):
        """Test now playing state when nothing is playing."""
        result = mock_karaoke.get_now_playing()

        assert result["now_playing"] is None
        assert result["now_playing_user"] is None
        assert result["now_playing_position"] is None
        assert result["up_next"] is None
        assert result["is_paused"] is True
        assert result["volume"] == 0.85

    def test_get_now_playing_with_song(self, mock_karaoke):
        """Test now playing state with active song."""
        mock_karaoke.now_playing = "Test Song"
        mock_karaoke.now_playing_user = "TestUser"
        mock_karaoke.now_playing_duration = 180
        mock_karaoke.now_playing_transpose = 2
        mock_karaoke.is_paused = False
        mock_karaoke.volume = 0.7

        result = mock_karaoke.get_now_playing()

        assert result["now_playing"] == "Test Song"
        assert result["now_playing_user"] == "TestUser"
        assert result["now_playing_duration"] == 180
        assert result["now_playing_position"] is None
        assert result["now_playing_transpose"] == 2
        assert result["is_paused"] is False
        assert result["volume"] == 0.7

    def test_get_now_playing_with_queue(self, mock_karaoke):
        """Test now playing shows up_next from queue."""
        mock_karaoke.queue_manager.enqueue("/songs/Next Song---abc.mp4", "NextUser")

        result = mock_karaoke.get_now_playing()

        assert result["up_next"] == "Next Song"
        assert result["next_user"] == "NextUser"


class TestResetNowPlaying:
    """Tests for the reset_now_playing method."""

    def test_reset_now_playing(self, mock_karaoke):
        """Test that reset clears all now playing state."""
        mock_karaoke.now_playing = "Test Song"
        mock_karaoke.now_playing_filename = "/songs/test.mp4"
        mock_karaoke.now_playing_user = "TestUser"
        mock_karaoke.now_playing_url = "http://localhost/stream"
        mock_karaoke.now_playing_transpose = 3
        mock_karaoke.now_playing_duration = 200
        mock_karaoke.is_paused = False
        mock_karaoke.is_playing = True

        mock_karaoke.reset_now_playing()

        assert mock_karaoke.now_playing is None
        assert mock_karaoke.now_playing_filename is None
        assert mock_karaoke.now_playing_user is None
        assert mock_karaoke.now_playing_url is None
        assert mock_karaoke.now_playing_transpose == 0
        assert mock_karaoke.now_playing_duration is None
        assert mock_karaoke.now_playing_position is None
        assert mock_karaoke.is_paused is True
        assert mock_karaoke.is_playing is False


class TestPause:
    """Tests for the pause method."""

    def test_pause_when_playing(self, mock_karaoke):
        """Test pausing when a song is playing."""
        mock_karaoke.is_playing = True
        mock_karaoke.is_paused = False
        mock_karaoke.now_playing = "Test Song"

        result = mock_karaoke.pause()

        assert result is True
        assert mock_karaoke.is_paused is True

    def test_resume_when_paused(self, mock_karaoke):
        """Test resuming when a song is paused."""
        mock_karaoke.is_playing = True
        mock_karaoke.is_paused = True
        mock_karaoke.now_playing = "Test Song"

        result = mock_karaoke.pause()

        assert result is True
        assert mock_karaoke.is_paused is False

    def test_pause_when_nothing_playing(self, mock_karaoke):
        """Test pause returns False when nothing is playing."""
        mock_karaoke.is_playing = False

        result = mock_karaoke.pause()

        assert result is False


class TestVolumeChange:
    """Tests for the volume_change method."""

    def test_set_volume(self, mock_karaoke):
        """Test setting volume to a specific level."""
        result = mock_karaoke.volume_change(0.5)

        assert result is True
        assert mock_karaoke.volume == 0.5

    def test_set_volume_max(self, mock_karaoke):
        """Test setting volume to maximum."""
        result = mock_karaoke.volume_change(1.0)

        assert result is True
        assert mock_karaoke.volume == 1.0

    def test_set_volume_min(self, mock_karaoke):
        """Test setting volume to minimum."""
        result = mock_karaoke.volume_change(0.0)

        assert result is True
        assert mock_karaoke.volume == 0.0


class TestVolUp:
    """Tests for the vol_up method."""

    def test_increase_volume(self, mock_karaoke):
        """Test increasing volume by 10%."""
        mock_karaoke.volume = 0.5

        mock_karaoke.vol_up()

        assert mock_karaoke.volume == 0.6

    def test_increase_near_max(self, mock_karaoke):
        """Test increasing volume near maximum."""
        mock_karaoke.volume = 0.95

        mock_karaoke.vol_up()

        assert mock_karaoke.volume == 1.05  # Goes slightly over but allowed


class TestVolDown:
    """Tests for the vol_down method."""

    def test_decrease_volume(self, mock_karaoke):
        """Test decreasing volume by 10%."""
        mock_karaoke.volume = 0.5

        mock_karaoke.vol_down()

        assert mock_karaoke.volume == 0.4

    def test_decrease_near_min(self, mock_karaoke):
        """Test decreasing volume near minimum."""
        mock_karaoke.volume = 0.05

        mock_karaoke.vol_down()

        # When volume < 0.1, it's set to 0 then decremented by 0.1
        assert mock_karaoke.volume == -0.1


class TestRestart:
    """Tests for the restart method."""

    def test_restart_when_playing(self, mock_karaoke):
        """Test restarting when a song is playing."""
        mock_karaoke.is_playing = True
        mock_karaoke.is_paused = True
        mock_karaoke.now_playing = "Test Song"

        result = mock_karaoke.restart()

        assert result is True
        assert mock_karaoke.is_paused is False

    def test_restart_when_nothing_playing(self, mock_karaoke):
        """Test restart returns False when nothing is playing."""
        mock_karaoke.is_playing = False

        result = mock_karaoke.restart()

        assert result is False


class TestStop:
    """Tests for the stop method."""

    def test_stop_sets_running_false(self, mock_karaoke):
        """Test that stop sets running to False."""
        mock_karaoke.running = True

        mock_karaoke.stop()

        assert mock_karaoke.running is False


class TestResetNowPlayingNotification:
    """Tests for the reset_now_playing_notification method."""

    def test_reset_notification(self, mock_karaoke):
        """Test that notification is reset to None."""
        mock_karaoke.now_playing_notification = "Some notification"

        mock_karaoke.reset_now_playing_notification()

        assert mock_karaoke.now_playing_notification is None
