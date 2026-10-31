"""Unit tests for DurationPopulator."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.duration_populator import DurationPopulator
from pikaraoke.lib.events import EventSystem


def _run_now(fn, *args):
    """Stand in for gevent.spawn, running the worker inline so tests stay sync."""
    fn(*args)


@pytest.fixture
def events():
    return EventSystem()


@pytest.fixture
def db():
    return MagicMock()


class TestPlaybackStarted:
    """Persist the figure the playback path already probed."""

    def test_writes_the_now_playing_duration(self, db, events):
        DurationPopulator(db, events, get_now_playing=lambda: ("/songs/a.mp4", 212))
        events.emit("playback_started")
        db.set_duration.assert_called_once_with("/songs/a.mp4", 212)

    def test_no_write_when_nothing_is_playing(self, db, events):
        DurationPopulator(db, events, get_now_playing=lambda: (None, None))
        events.emit("playback_started")
        db.set_duration.assert_not_called()

    def test_no_write_when_duration_is_unknown(self, db, events):
        DurationPopulator(db, events, get_now_playing=lambda: ("/songs/a.mp4", None))
        events.emit("playback_started")
        db.set_duration.assert_not_called()


class TestSongQueued:
    """Probe a queued song while it waits, off the playback path."""

    def _populator(self, db, events):
        return DurationPopulator(db, events, get_now_playing=lambda: (None, None), spawn=_run_now)

    def test_probes_and_writes(self, db, events):
        db.get_duration.return_value = None
        self._populator(db, events)
        with patch(
            "pikaraoke.lib.duration_populator.get_media_duration", return_value=180.0
        ) as probe:
            events.emit("song_queued", "/songs/b.mp4")
        probe.assert_called_once_with("/songs/b.mp4")
        db.set_duration.assert_called_once_with("/songs/b.mp4", 180.0)

    def test_skips_write_when_probe_fails(self, db, events):
        db.get_duration.return_value = None
        self._populator(db, events)
        with patch("pikaraoke.lib.duration_populator.get_media_duration", return_value=None):
            events.emit("song_queued", "/songs/b.mp4")
        db.set_duration.assert_not_called()

    def test_does_not_probe_a_row_already_filled(self, db, events):
        """A downloaded song fills its row at register, so queuing it must not re-probe."""
        db.get_duration.return_value = 200
        self._populator(db, events)
        with patch("pikaraoke.lib.duration_populator.get_media_duration") as probe:
            events.emit("song_queued", "/songs/b.mp4")
        probe.assert_not_called()
        db.set_duration.assert_not_called()

    def test_does_not_probe_a_zip(self, db, events):
        """A zip's audio is inside the archive; it is filled at play, not at queue."""
        self._populator(db, events)
        with patch("pikaraoke.lib.duration_populator.get_media_duration") as probe:
            events.emit("song_queued", "/songs/Song---abc12345678.zip")
        probe.assert_not_called()
        db.get_duration.assert_not_called()
        db.set_duration.assert_not_called()

    def test_emits_now_playing_update_after_writing(self, db, events):
        """So a waiting song's end time renders once its length lands."""
        db.get_duration.return_value = None
        updates = []
        events.on("now_playing_update", lambda: updates.append(True))
        self._populator(db, events)
        with patch("pikaraoke.lib.duration_populator.get_media_duration", return_value=180.0):
            events.emit("song_queued", "/songs/b.mp4")
        assert updates == [True]

    def test_no_now_playing_update_when_nothing_is_written(self, db, events):
        db.get_duration.return_value = None
        updates = []
        events.on("now_playing_update", lambda: updates.append(True))
        self._populator(db, events)
        with patch("pikaraoke.lib.duration_populator.get_media_duration", return_value=None):
            events.emit("song_queued", "/songs/b.mp4")
        assert updates == []

    def test_drains_a_backlog_of_queued_songs(self, db, events):
        """Several songs queued together are all probed by the one worker."""
        db.get_duration.return_value = None
        self._populator(db, events)
        with patch(
            "pikaraoke.lib.duration_populator.get_media_duration", return_value=180.0
        ) as probe:
            events.emit("song_queued", "/songs/a.mp4")
            events.emit("song_queued", "/songs/b.mp4")
            events.emit("song_queued", "/songs/c.mp4")
        assert probe.call_count == 3
        assert db.set_duration.call_count == 3
