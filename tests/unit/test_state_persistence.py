"""Tests for StatePersistence and Karaoke._restore_state/_persist_state."""

import json
import os
import time
from unittest.mock import patch

import pytest

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.state_persistence import SCHEMA_VERSION, StatePersistence


class TestStatePersistence:
    def test_save_then_load_round_trip(self, tmp_path):
        sp = StatePersistence(path=str(tmp_path / "state.json"))
        payload = {
            "saved_at": 123.0,
            "volume": 0.7,
            "queue": [{"user": "alice", "file": "/x.mp4", "title": "x", "semitones": 0}],
            "now_playing": {
                "filename": "/y.mp4",
                "user": "bob",
                "transpose": 2,
                "duration": 180.0,
                "position": 42.5,
                "position_updated_at": 100.0,
                "is_paused": False,
            },
        }
        sp.save(payload)
        loaded = sp.load()
        assert loaded is not None
        assert loaded["version"] == SCHEMA_VERSION
        assert loaded["volume"] == 0.7
        assert loaded["now_playing"]["position"] == 42.5
        assert loaded["queue"][0]["user"] == "alice"

    def test_load_missing_file_returns_none(self, tmp_path):
        sp = StatePersistence(path=str(tmp_path / "missing.json"))
        assert sp.load() is None

    def test_load_corrupt_json_returns_none(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not valid json {")
        assert StatePersistence(path=str(path)).load() is None

    def test_load_wrong_version_returns_none(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"version": 999, "queue": []}))
        assert StatePersistence(path=str(path)).load() is None

    def test_save_is_atomic(self, tmp_path):
        """os.replace failure must leave the original file intact."""
        path = tmp_path / "state.json"
        sp = StatePersistence(path=str(path))
        sp.save({"queue": [], "volume": 0.5})
        original = path.read_text()

        with patch("pikaraoke.lib.state_persistence.os.replace", side_effect=OSError("boom")):
            sp.save({"queue": [{"user": "x", "file": "/a", "title": "a", "semitones": 0}]})

        assert path.read_text() == original
        # No stray tempfiles
        leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".pikaraoke_state.")]
        assert leftovers == []


class _Harness:
    """Tiny Karaoke-shaped object that can run _restore_state / _persist_state."""

    def __init__(self, tmp_path):
        from pikaraoke.lib.events import EventSystem
        from pikaraoke.lib.preference_manager import PreferenceManager
        from pikaraoke.lib.queue_manager import QueueManager

        class _StubPC:
            now_playing = None
            now_playing_filename = None
            now_playing_user = None
            now_playing_transpose = 0
            now_playing_duration = None
            now_playing_position = None
            position_updated_at = None
            pending_resume_position = None
            is_paused = True

        class _StubSongManager:
            @staticmethod
            def display_name_from_path(path, remove_youtube_id=False):
                return os.path.basename(path)

        self.events = EventSystem()
        self.preferences = PreferenceManager(
            config_file_path=str(tmp_path / "config.ini"), target=self
        )
        self.playback_controller = _StubPC()
        self.song_manager = _StubSongManager()
        self.queue_manager = QueueManager(preferences=self.preferences, events=self.events)
        self.volume = 0.85
        self.state_persistence = StatePersistence(path=str(tmp_path / "state.json"))

    _restore_state = Karaoke._restore_state
    _persist_state = Karaoke._persist_state


class TestKaraokeRestore:
    def test_restore_missing_state_is_noop(self, tmp_path):
        k = _Harness(tmp_path)
        k._restore_state()
        assert k.queue_manager.queue == []
        assert k.volume == 0.85
        assert k.playback_controller.pending_resume_position is None

    def test_restore_queue_only(self, tmp_path):
        k = _Harness(tmp_path)
        song = tmp_path / "song.mp4"
        song.write_text("")
        k.state_persistence.save(
            {
                "volume": 0.5,
                "queue": [{"user": "u", "file": str(song), "title": "t", "semitones": 0}],
                "now_playing": None,
            }
        )
        k._restore_state()
        assert k.volume == 0.5
        assert len(k.queue_manager.queue) == 1
        assert k.playback_controller.pending_resume_position is None

    def test_restore_drops_queue_items_whose_files_are_missing(self, tmp_path):
        k = _Harness(tmp_path)
        existing = tmp_path / "present.mp4"
        existing.write_text("")
        k.state_persistence.save(
            {
                "queue": [
                    {"user": "u", "file": str(existing), "title": "a", "semitones": 0},
                    {"user": "u", "file": "/nope.mp4", "title": "b", "semitones": 0},
                ],
                "now_playing": None,
            }
        )
        k._restore_state()
        assert [i["file"] for i in k.queue_manager.queue] == [str(existing)]

    def test_restore_computes_elapsed_resume_position(self, tmp_path):
        k = _Harness(tmp_path)
        song = tmp_path / "song.mp4"
        song.write_text("")
        k.state_persistence.save(
            {
                "queue": [],
                "now_playing": {
                    "filename": str(song),
                    "user": "alice",
                    "transpose": 0,
                    "duration": 300.0,
                    "position": 40.0,
                    "position_updated_at": 1000.0,
                    "is_paused": False,
                },
            }
        )
        with patch("pikaraoke.karaoke.time.time", return_value=1005.0):
            k._restore_state()
        assert k.queue_manager.queue[0]["file"] == str(song)
        assert k.playback_controller.pending_resume_position == pytest.approx(45.0)

    def test_restore_preserves_position_when_paused(self, tmp_path):
        k = _Harness(tmp_path)
        song = tmp_path / "song.mp4"
        song.write_text("")
        k.state_persistence.save(
            {
                "queue": [],
                "now_playing": {
                    "filename": str(song),
                    "user": "alice",
                    "transpose": 0,
                    "duration": 300.0,
                    "position": 30.0,
                    "position_updated_at": 1000.0,
                    "is_paused": True,
                },
            }
        )
        with patch("pikaraoke.karaoke.time.time", return_value=9999.0):
            k._restore_state()
        assert k.playback_controller.pending_resume_position == pytest.approx(30.0)

    def test_restore_skips_song_when_nearly_finished(self, tmp_path):
        k = _Harness(tmp_path)
        song = tmp_path / "song.mp4"
        song.write_text("")
        k.state_persistence.save(
            {
                "queue": [],
                "now_playing": {
                    "filename": str(song),
                    "user": "alice",
                    "transpose": 0,
                    "duration": 100.0,
                    "position": 99.5,
                    "position_updated_at": time.time(),
                    "is_paused": False,
                },
            }
        )
        k._restore_state()
        assert k.queue_manager.queue == []
        assert k.playback_controller.pending_resume_position is None

    def test_restore_skips_now_playing_when_file_missing(self, tmp_path):
        k = _Harness(tmp_path)
        k.state_persistence.save(
            {
                "queue": [],
                "now_playing": {
                    "filename": "/absolutely/not/here.mp4",
                    "user": "alice",
                    "transpose": 0,
                    "duration": 100.0,
                    "position": 10.0,
                    "position_updated_at": 1000.0,
                    "is_paused": False,
                },
            }
        )
        k._restore_state()
        assert k.queue_manager.queue == []
        assert k.playback_controller.pending_resume_position is None


class TestKaraokePersist:
    def test_persist_captures_queue_and_volume(self, tmp_path):
        k = _Harness(tmp_path)
        k.volume = 0.42
        k.queue_manager.queue = [{"user": "u", "file": "/a", "title": "a", "semitones": 0}]
        k._persist_state()
        loaded = k.state_persistence.load()
        assert loaded["volume"] == 0.42
        assert loaded["queue"] == k.queue_manager.queue
        assert loaded["now_playing"] is None

    def test_persist_captures_now_playing(self, tmp_path):
        k = _Harness(tmp_path)
        pc = k.playback_controller
        pc.now_playing_filename = "/x.mp4"
        pc.now_playing_user = "alice"
        pc.now_playing_transpose = -2
        pc.now_playing_duration = 200.0
        pc.now_playing_position = 77.0
        pc.position_updated_at = 1234.5
        pc.is_paused = False
        k._persist_state()
        loaded = k.state_persistence.load()
        np = loaded["now_playing"]
        assert np["filename"] == "/x.mp4"
        assert np["user"] == "alice"
        assert np["transpose"] == -2
        assert np["position"] == 77.0
        assert np["is_paused"] is False
