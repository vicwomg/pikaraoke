"""Unit tests for Karaoke utility methods."""

import json
import threading
from unittest.mock import MagicMock


class TestConvertPreferenceValue:
    """Tests for the _convert_value method (now in PreferenceManager)."""

    def test_convert_true_values(self):
        """Test conversion of truthy string values."""
        from pikaraoke.lib.preference_manager import PreferenceManager

        prefs = PreferenceManager()
        assert prefs._convert_value("true") is True
        assert prefs._convert_value("True") is True
        assert prefs._convert_value("TRUE") is True
        assert prefs._convert_value("yes") is True
        assert prefs._convert_value("on") is True

    def test_convert_false_values(self):
        """Test conversion of falsy string values."""
        from pikaraoke.lib.preference_manager import PreferenceManager

        prefs = PreferenceManager()
        assert prefs._convert_value("false") is False
        assert prefs._convert_value("False") is False
        assert prefs._convert_value("FALSE") is False
        assert prefs._convert_value("no") is False
        assert prefs._convert_value("off") is False

    def test_convert_integer_values(self):
        """Test conversion of integer string values."""
        from pikaraoke.lib.preference_manager import PreferenceManager

        prefs = PreferenceManager()
        assert prefs._convert_value("42") == 42
        assert prefs._convert_value("0") == 0
        assert prefs._convert_value("-5") == -5

    def test_convert_float_values(self):
        """Test conversion of float string values."""
        from pikaraoke.lib.preference_manager import PreferenceManager

        prefs = PreferenceManager()
        assert prefs._convert_value("3.14") == 3.14
        assert prefs._convert_value("0.5") == 0.5
        assert prefs._convert_value("-2.5") == -2.5

    def test_convert_string_passthrough(self):
        """Test that non-special strings pass through unchanged."""
        from pikaraoke.lib.preference_manager import PreferenceManager

        prefs = PreferenceManager()
        assert prefs._convert_value("hello") == "hello"
        assert prefs._convert_value("some text") == "some text"
        assert prefs._convert_value("/path/to/file") == "/path/to/file"

    def test_convert_non_string_passthrough(self):
        """Test that non-string values pass through unchanged."""
        from pikaraoke.lib.preference_manager import PreferenceManager

        prefs = PreferenceManager()
        assert prefs._convert_value(42) == 42
        assert prefs._convert_value(3.14) == 3.14
        assert prefs._convert_value(True) is True
        assert prefs._convert_value(None) is None


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
        pc = mock_karaoke.playback_controller
        pc.now_playing = "Test Song"
        pc.now_playing_user = "TestUser"
        pc.now_playing_duration = 180
        pc.now_playing_transpose = 2
        pc.is_paused = False
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
        mock_karaoke.queue_manager.enqueue("/songs/Next Song---dQw4w9WgXcQ.mp4", "NextUser")

        result = mock_karaoke.get_now_playing()

        assert result["up_next"] == "Next Song"
        assert result["next_user"] == "NextUser"


class TestResetNowPlaying:
    """Tests for the reset_now_playing method."""

    def test_reset_now_playing(self, mock_karaoke):
        """Test that reset clears all now playing state."""
        pc = mock_karaoke.playback_controller
        pc.now_playing = "Test Song"
        pc.now_playing_filename = "/songs/test.mp4"
        pc.now_playing_user = "TestUser"
        pc.now_playing_url = "http://localhost/stream"
        pc.now_playing_transpose = 3
        pc.now_playing_duration = 200
        pc.is_paused = False
        pc.is_playing = True

        mock_karaoke.reset_now_playing()

        assert pc.now_playing is None
        assert pc.now_playing_filename is None
        assert pc.now_playing_user is None
        assert pc.now_playing_url is None
        assert pc.now_playing_transpose == 0
        assert pc.now_playing_duration is None
        assert pc.now_playing_position is None
        assert pc.is_paused is True
        assert pc.is_playing is False

    def test_reset_now_playing_resets_volume_to_preference(self, mock_karaoke):
        """Test that reset restores volume to user's saved preference."""
        # Set a custom volume preference
        mock_karaoke.preferences.set("volume", "0.7")

        # Change volume during playback
        mock_karaoke.volume = 0.3

        # Reset should restore volume to preference value
        mock_karaoke.reset_now_playing()

        assert mock_karaoke.volume == 0.7

    def test_reset_now_playing_resets_volume_to_default_when_no_preference(self, mock_karaoke):
        """Test that reset uses default volume when no preference is set."""
        # Ensure no volume preference is set (using defaults)
        # Clear any existing preference that might have been set
        current_volume_pref = mock_karaoke.preferences.get("volume")
        if current_volume_pref is not None:
            # Remove the preference by clearing and reloading defaults
            mock_karaoke.preferences.clear()

        # Change volume during playback
        mock_karaoke.volume = 0.3

        # Reset should restore volume to default (0.85)
        mock_karaoke.reset_now_playing()

        assert mock_karaoke.volume == 0.85


class TestPause:
    """Tests for the pause method."""

    def test_pause_when_playing(self, mock_karaoke):
        """Test pausing when a song is playing."""
        pc = mock_karaoke.playback_controller
        pc.is_playing = True
        pc.is_paused = False
        pc.now_playing = "Test Song"

        result = pc.pause()

        assert result is True
        assert pc.is_paused is True

    def test_resume_when_paused(self, mock_karaoke):
        """Test resuming when a song is paused."""
        pc = mock_karaoke.playback_controller
        pc.is_playing = True
        pc.is_paused = True
        pc.now_playing = "Test Song"

        result = pc.pause()

        assert result is True
        assert pc.is_paused is False

    def test_pause_when_nothing_playing(self, mock_karaoke):
        """Test pause returns False when nothing is playing."""
        mock_karaoke.playback_controller.is_playing = False

        result = mock_karaoke.playback_controller.pause()

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

    def test_mirrors_into_stem_volumes(self, mock_karaoke):
        """Single slider should mirror into both stems so the pre-stems level
        carries over to the dual-slider UI when stems become audible."""
        mock_karaoke.vocal_volume = 0.1
        mock_karaoke.instrumental_volume = 0.9

        mock_karaoke.volume_change(0.42)

        assert mock_karaoke.vocal_volume == 0.42
        assert mock_karaoke.instrumental_volume == 0.42


class TestVolUp:
    """Tests for the vol_up method."""

    def test_increase_volume(self, mock_karaoke):
        """Test increasing volume by 10%."""
        mock_karaoke.volume = 0.5

        mock_karaoke.vol_up()

        assert mock_karaoke.volume == 0.6

    def test_increase_near_max(self, mock_karaoke):
        """Test increasing volume is clamped to 1.0."""
        mock_karaoke.volume = 0.95

        mock_karaoke.vol_up()

        assert mock_karaoke.volume == 1.0


class TestVolDown:
    """Tests for the vol_down method."""

    def test_decrease_volume(self, mock_karaoke):
        """Test decreasing volume by 10%."""
        mock_karaoke.volume = 0.5

        mock_karaoke.vol_down()

        assert mock_karaoke.volume == 0.4

    def test_decrease_near_min(self, mock_karaoke):
        """Test decreasing volume is clamped to 0.0."""
        mock_karaoke.volume = 0.05

        mock_karaoke.vol_down()

        assert mock_karaoke.volume == 0.0


class TestRestart:
    """Tests for the restart method."""

    def test_restart_when_playing(self, mock_karaoke):
        """Test restarting when a song is playing."""
        pc = mock_karaoke.playback_controller
        pc.is_playing = True
        pc.is_paused = True
        pc.now_playing = "Test Song"
        pc.now_playing_position = 42.0

        result = mock_karaoke.restart()

        assert result is True
        assert pc.is_paused is False
        # Server-side position rewinds so pilots landing mid-restart see 0,
        # not the stale pre-restart value.
        assert pc.now_playing_position == 0.0

    def test_restart_broadcasts_seek_zero(self, mock_karaoke):
        """Restart emits seek=0 so clients that missed the restart click rewind too."""
        from unittest.mock import MagicMock

        sio = MagicMock()
        mock_karaoke.socketio = sio

        pc = mock_karaoke.playback_controller
        pc.is_playing = True
        pc.now_playing_position = 42.0

        mock_karaoke.restart()

        seek_calls = [c for c in sio.emit.call_args_list if c.args and c.args[0] == "seek"]
        assert seek_calls, "expected a 'seek' emit from restart()"
        assert seek_calls[0].args[1] == 0.0

    def test_restart_when_nothing_playing(self, mock_karaoke):
        """Test restart returns False when nothing is playing."""
        mock_karaoke.playback_controller.is_playing = False

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


class TestInitialReprocessWithWhisperx:
    """US-17: the library reprocess must fire at most once per install."""

    @staticmethod
    def _wire(mock_karaoke, has_aligner=True, sentinel_set=False):
        store: dict[str, str] = {}
        if sentinel_set:
            store[mock_karaoke._WHISPERX_REPROCESS_SENTINEL] = "1"
        db = MagicMock()
        db.get_metadata.side_effect = lambda key: store.get(key)
        db.set_metadata.side_effect = lambda key, value: store.__setitem__(key, value)
        mock_karaoke.db = db

        ls = MagicMock()
        ls.has_aligner = has_aligner
        mock_karaoke.lyrics_service = ls

        mock_karaoke.song_manager = MagicMock()
        mock_karaoke.song_manager.songs = []
        return store, db, ls

    def test_runs_on_first_startup_and_sets_sentinel(self, mock_karaoke):
        store, _db, ls = self._wire(mock_karaoke)
        mock_karaoke._maybe_initial_reprocess_with_whisperx()
        ls.reprocess_library.assert_called_once()
        assert store[mock_karaoke._WHISPERX_REPROCESS_SENTINEL] == "1"

    def test_skips_when_sentinel_already_set(self, mock_karaoke):
        _store, _db, ls = self._wire(mock_karaoke, sentinel_set=True)
        mock_karaoke._maybe_initial_reprocess_with_whisperx()
        ls.reprocess_library.assert_not_called()

    def test_skips_when_no_aligner(self, mock_karaoke):
        store, _db, ls = self._wire(mock_karaoke, has_aligner=False)
        mock_karaoke._maybe_initial_reprocess_with_whisperx()
        ls.reprocess_library.assert_not_called()
        # No sentinel write either — we haven't actually offered an upgrade.
        assert mock_karaoke._WHISPERX_REPROCESS_SENTINEL not in store


class TestSongWarningBuffer:
    """US-39: the song_warning listener persists to DB and caps length."""

    @staticmethod
    def _wire(mock_karaoke, stored: dict[str, str] | None = None):
        """Attach a stubbed DB and an empty rolling warning buffer."""
        stored = stored if stored is not None else {}
        db = MagicMock()
        db.get_metadata.side_effect = lambda key: stored.get(key)
        db.set_metadata.side_effect = lambda key, value: stored.__setitem__(key, value)
        mock_karaoke.db = db
        mock_karaoke._song_warnings = mock_karaoke._load_song_warnings()
        mock_karaoke._song_warnings_lock = threading.Lock()
        return stored

    def test_handle_song_warning_appends_and_persists(self, mock_karaoke):
        """Calling the listener pushes the event into the buffer and flushes."""
        store = self._wire(mock_karaoke)
        mock_karaoke.socketio = MagicMock()

        payload = {"message": "Vocal separation failed", "severity": "warning", "song": "x.mp4"}
        mock_karaoke._handle_song_warning(payload)

        saved = json.loads(store["song_warnings"])
        assert len(saved) == 1
        assert saved[0]["message"] == "Vocal separation failed"
        assert "timestamp" in saved[0]
        mock_karaoke.socketio.emit.assert_called_once()

    def test_loads_persisted_on_init(self, mock_karaoke):
        """Pre-existing buffer entries are restored from the DB."""
        prior = [{"message": "boom", "severity": "error", "timestamp": 1700000000.0}]
        self._wire(mock_karaoke, {"song_warnings": json.dumps(prior)})
        assert mock_karaoke.get_song_warnings() == prior

    def test_buffer_caps_at_max(self, mock_karaoke):
        """Buffer keeps only the last N entries to avoid unbounded growth."""
        self._wire(mock_karaoke)
        max_entries = mock_karaoke._SONG_WARNINGS_MAX
        for i in range(max_entries + 5):
            mock_karaoke._handle_song_warning({"message": f"w{i}"})
        warnings = mock_karaoke.get_song_warnings()
        assert len(warnings) == max_entries
        # Oldest was dropped; newest is kept.
        assert warnings[-1]["message"] == f"w{max_entries + 4}"

    def test_clear_song_warnings_wipes_buffer_and_store(self, mock_karaoke):
        store = self._wire(mock_karaoke)
        mock_karaoke._handle_song_warning({"message": "w"})
        assert mock_karaoke.get_song_warnings()

        mock_karaoke.clear_song_warnings()

        assert mock_karaoke.get_song_warnings() == []
        assert json.loads(store["song_warnings"]) == []

    def test_dismiss_song_warnings_removes_matching_and_broadcasts(self, mock_karaoke):
        """US-13: per-song dismiss drops matching entries and emits broadcast."""
        store = self._wire(mock_karaoke)
        mock_karaoke.socketio = MagicMock()
        mock_karaoke._handle_song_warning({"message": "a", "song": "SongA.mp4"})
        mock_karaoke._handle_song_warning({"message": "b", "song": "SongB.mp4"})
        mock_karaoke._handle_song_warning({"message": "a2", "song": "SongA.mp4"})

        removed = mock_karaoke.dismiss_song_warnings("SongA.mp4")

        assert removed == 2
        remaining = mock_karaoke.get_song_warnings()
        assert [w["song"] for w in remaining] == ["SongB.mp4"]
        # Persisted buffer reflects the dismissal.
        assert [w["song"] for w in json.loads(store["song_warnings"])] == ["SongB.mp4"]
        # Broadcast fires once, carrying just the song key.
        dismiss_calls = [
            c
            for c in mock_karaoke.socketio.emit.call_args_list
            if c.args[0] == "song_warnings_dismissed"
        ]
        assert len(dismiss_calls) == 1
        assert dismiss_calls[0].args[1] == {"song": "SongA.mp4"}

    def test_dismiss_song_warnings_no_match_is_noop(self, mock_karaoke):
        """Dismissing a song with no buffered entries returns 0 and skips write."""
        store = self._wire(mock_karaoke)
        mock_karaoke.socketio = MagicMock()
        mock_karaoke._handle_song_warning({"message": "x", "song": "Kept.mp4"})
        store_writes_before = len(store)

        removed = mock_karaoke.dismiss_song_warnings("Missing.mp4")

        assert removed == 0
        assert len(mock_karaoke.get_song_warnings()) == 1
        # No redundant DB write when nothing matched.
        assert len(store) == store_writes_before
        # Broadcast still fires so other clients can drop anything they have
        # locally — the server is authoritative for dismissal, not the buffer.
        dismiss_calls = [
            c
            for c in mock_karaoke.socketio.emit.call_args_list
            if c.args[0] == "song_warnings_dismissed"
        ]
        assert len(dismiss_calls) == 1
