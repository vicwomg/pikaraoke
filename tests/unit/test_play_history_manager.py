"""Unit tests for PlayHistoryManager."""

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.play_history_manager import PlayHistoryManager


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def events():
    return EventSystem()


@pytest.fixture
def history(db, events):
    return PlayHistoryManager(db=db, events=events)


@pytest.fixture
def song_id(db):
    """A song in the library, for plays to point at."""
    db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
    return db.get_song_id_by_path("/songs/a.mp4")


class TestSessions:
    def test_no_current_session_initially(self, history):
        assert history.get_current_session() is None

    def test_start_session_returns_uuid(self, history):
        session_uuid = history.start_session("Friday Night")
        assert history.get_current_session()["uuid"] == session_uuid

    def test_start_session_unnamed(self, history):
        history.start_session()
        assert history.get_current_session()["name"] is None

    def test_end_session_clears_current(self, history):
        history.start_session()
        history.end_session()
        assert history.get_current_session() is None

    def test_end_session_with_none_active_is_noop(self, history):
        history.end_session()
        assert history.get_current_session() is None

    def test_starting_a_session_closes_the_open_one(self, history):
        first = history.start_session("First")
        second = history.start_session("Second")

        assert history.get_current_session()["uuid"] == second
        ended = [s for s in history.get_sessions() if s["uuid"] == first][0]
        assert ended["ended_at"] is not None

    def test_rename_session(self, history):
        session_uuid = history.start_session()
        assert history.rename_session(session_uuid, "Saturday") is True
        assert history.get_current_session()["name"] == "Saturday"

    def test_rename_unknown_session(self, history):
        assert history.rename_session("no-such-uuid", "Nope") is False

    def test_get_sessions_includes_play_counts(self, history, song_id):
        history.start_session("Night One")
        history.record_play(song_id, "Alice")
        history.record_play(song_id, "Bob")

        sessions = history.get_sessions()
        assert len(sessions) == 1
        assert sessions[0]["name"] == "Night One"
        assert sessions[0]["play_count"] == 2

    def test_get_sessions_counts_zero_for_empty_session(self, history):
        history.start_session("Quiet Night")
        assert history.get_sessions()[0]["play_count"] == 0


class TestRecordPlay:
    def test_auto_starts_session_when_none_active(self, history, song_id):
        history.record_play(song_id, "Alice")

        session = history.get_current_session()
        assert session is not None
        assert session["name"] is None
        assert history.get_sessions()[0]["play_count"] == 1

    def test_reuses_the_active_session(self, history, song_id):
        session_uuid = history.start_session("Night One")
        history.record_play(song_id, "Alice")
        history.record_play(song_id, "Bob")

        assert history.get_current_session()["uuid"] == session_uuid
        assert len(history.get_sessions()) == 1

    def test_auto_started_session_can_be_renamed_afterwards(self, history, song_id):
        history.record_play(song_id, "Alice")
        session_uuid = history.get_current_session()["uuid"]

        history.rename_session(session_uuid, "The Night We Forgot To Start")

        assert history.get_current_session()["name"] == "The Night We Forgot To Start"

    def test_records_performer_and_song(self, history, song_id):
        history.record_play(song_id, "Alice")

        play = history.get_plays()[0]
        assert play["performer"] == "Alice"
        assert play["song_id"] == song_id
        assert play["file_path"] == "/songs/a.mp4"

    def test_song_id_may_be_none(self, history):
        history.record_play(None, "Alice")

        play = history.get_plays()[0]
        assert play["song_id"] is None
        assert play["performer"] == "Alice"


class TestCompleted:
    def test_not_completed_before_song_ends(self, history, song_id):
        history.record_play(song_id, "Alice")
        assert history.get_plays()[0]["completed"] == 0

    def test_completed_on_complete_reason(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "complete")
        assert history.get_plays()[0]["completed"] == 1

    @pytest.mark.parametrize("reason", ["skip", "timeout", None])
    def test_not_completed_on_other_reasons(self, history, events, song_id, reason):
        """The regression that would make `completed` a constant 1 and useless."""
        history.record_play(song_id, "Alice")
        events.emit("song_ended", reason)
        assert history.get_plays()[0]["completed"] == 0

    def test_song_ended_with_nothing_tracked_is_noop(self, history, events):
        events.emit("song_ended", "complete")
        assert history.get_plays() == []

    def test_second_song_ended_does_not_complete_the_previous_play(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "skip")
        events.emit("song_ended", "complete")

        assert history.get_plays()[0]["completed"] == 0


class TestGetSingers:
    def test_empty_when_nothing_played(self, history):
        assert history.get_singers() == []

    def test_counts_per_performer(self, history, song_id):
        history.record_play(song_id, "Alice")
        history.record_play(song_id, "Alice")
        history.record_play(song_id, "Bob")

        singers = history.get_singers()
        assert [(s["performer"], s["play_count"]) for s in singers] == [("Alice", 2), ("Bob", 1)]

    def test_groups_casing_variants_as_one_performer(self, history, song_id):
        history.record_play(song_id, "Mike")
        history.record_play(song_id, "mike")
        history.record_play(song_id, "MIKE")

        singers = history.get_singers()
        assert len(singers) == 1
        assert singers[0]["play_count"] == 3

    def test_uses_most_recent_casing(self, history, song_id):
        history.record_play(song_id, "mike")
        history.record_play(song_id, "Mike")

        assert history.get_singers()[0]["performer"] == "Mike"


class TestGetTopSongs:
    def test_empty_when_nothing_played(self, history):
        assert history.get_top_songs() == []

    def test_ranks_by_play_count(self, db, history):
        db.insert_songs(
            [
                {"file_path": "/songs/popular.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/rare.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        popular = db.get_song_id_by_path("/songs/popular.mp4")
        rare = db.get_song_id_by_path("/songs/rare.mp4")

        history.record_play(popular, "Alice")
        history.record_play(popular, "Bob")
        history.record_play(rare, "Alice")

        top = history.get_top_songs()
        assert [(s["file_path"], s["play_count"]) for s in top] == [
            ("/songs/popular.mp4", 2),
            ("/songs/rare.mp4", 1),
        ]

    def test_respects_limit(self, db, history):
        for i in range(5):
            db.insert_songs([{"file_path": f"/songs/{i}.mp4", "youtube_id": None, "format": "mp4"}])
            history.record_play(db.get_song_id_by_path(f"/songs/{i}.mp4"), "Alice")

        assert len(history.get_top_songs(limit=3)) == 3

    def test_omits_plays_with_no_song(self, history):
        history.record_play(None, "Alice")
        assert history.get_top_songs() == []


class TestGetPlays:
    def test_newest_first(self, db, history):
        db.insert_songs(
            [
                {"file_path": "/songs/first.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/second.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        history.record_play(db.get_song_id_by_path("/songs/first.mp4"), "Alice")
        history.record_play(db.get_song_id_by_path("/songs/second.mp4"), "Bob")

        assert [p["performer"] for p in history.get_plays()] == ["Bob", "Alice"]

    def test_filters_by_session(self, history, song_id):
        first = history.start_session("One")
        history.record_play(song_id, "Alice")
        second = history.start_session("Two")
        history.record_play(song_id, "Bob")

        assert [p["performer"] for p in history.get_plays(first)] == ["Alice"]
        assert [p["performer"] for p in history.get_plays(second)] == ["Bob"]
        assert len(history.get_plays()) == 2

    def test_pagination(self, history, song_id):
        for name in ["A", "B", "C", "D", "E"]:
            history.record_play(song_id, name)

        page = history.get_plays(limit=2, offset=0)
        next_page = history.get_plays(limit=2, offset=2)

        assert len(page) == 2
        assert len(next_page) == 2
        assert {p["id"] for p in page}.isdisjoint({p["id"] for p in next_page})

    def test_count_plays(self, history, song_id):
        session_uuid = history.start_session("One")
        history.record_play(song_id, "Alice")
        history.start_session("Two")
        history.record_play(song_id, "Bob")

        assert history.count_plays() == 2
        assert history.count_plays(session_uuid) == 1


class TestDelete:
    def test_delete_play(self, history, song_id):
        history.record_play(song_id, "Alice")
        play_id = history.get_plays()[0]["id"]

        assert history.delete_play(play_id) is True
        assert history.get_plays() == []

    def test_delete_unknown_play(self, history):
        assert history.delete_play(999) is False

    def test_delete_session_cascades_to_plays(self, history, song_id):
        session_uuid = history.start_session("One")
        history.record_play(song_id, "Alice")

        assert history.delete_session(session_uuid) is True
        assert history.get_sessions() == []
        assert history.get_plays() == []

    def test_delete_unknown_session(self, history):
        assert history.delete_session("no-such-uuid") is False

    def test_delete_session_leaves_other_sessions_alone(self, history, song_id):
        first = history.start_session("One")
        history.record_play(song_id, "Alice")
        history.start_session("Two")
        history.record_play(song_id, "Bob")

        history.delete_session(first)

        assert [p["performer"] for p in history.get_plays()] == ["Bob"]


class TestExport:
    def test_exports_session_plays_oldest_first(self, history, events, song_id):
        session_uuid = history.start_session("One")
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "complete")
        history.record_play(song_id, "Bob")
        events.emit("song_ended", "skip")

        rows = history.export_plays(session_uuid)

        assert [r["performer"] for r in rows] == ["Alice", "Bob"]
        assert [r["completed"] for r in rows] == [1, 0]
        assert rows[0]["file_path"] == "/songs/a.mp4"

    def test_excludes_other_sessions(self, history, song_id):
        session_uuid = history.start_session("One")
        history.record_play(song_id, "Alice")
        history.start_session("Two")
        history.record_play(song_id, "Bob")

        assert [r["performer"] for r in history.export_plays(session_uuid)] == ["Alice"]

    def test_unknown_session_exports_nothing(self, history):
        assert history.export_plays("no-such-uuid") == []
