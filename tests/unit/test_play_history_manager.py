"""Unit tests for PlayHistoryManager."""

from datetime import datetime, timezone

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

    def test_get_sessions_exposes_id(self, history):
        """The UI labels unnamed sessions by number, so id has to come back."""
        history.start_session()
        assert history.get_sessions()[0]["id"] is not None


class TestSessionPaging:
    """The session list pages. Without it older sessions could never be viewed
    or deleted, yet would still count towards the rankings."""

    @pytest.fixture
    def many_sessions(self, history):
        for n in range(1, 13):
            history.start_session(f"Night {n}")
        return 12

    def test_count_sessions(self, history, many_sessions):
        assert history.count_sessions() == many_sessions

    def test_count_sessions_is_zero_initially(self, history):
        assert history.count_sessions() == 0

    def test_first_page_is_the_newest(self, history, many_sessions):
        sessions = history.get_sessions(limit=5)
        assert [s["name"] for s in sessions] == [f"Night {n}" for n in range(12, 7, -1)]

    def test_offset_walks_back_through_older_sessions(self, history, many_sessions):
        assert [s["name"] for s in history.get_sessions(limit=5, offset=5)] == [
            f"Night {n}" for n in range(7, 2, -1)
        ]

    def test_last_page_is_short(self, history, many_sessions):
        assert [s["name"] for s in history.get_sessions(limit=5, offset=10)] == [
            "Night 2",
            "Night 1",
        ]

    def test_every_session_is_reachable_by_paging(self, history, many_sessions):
        seen = []
        for offset in range(0, many_sessions, 5):
            seen += [s["name"] for s in history.get_sessions(limit=5, offset=offset)]
        assert sorted(seen) == sorted(f"Night {n}" for n in range(1, 13))

    def test_past_the_end_is_empty(self, history, many_sessions):
        assert history.get_sessions(limit=5, offset=99) == []


class TestActivateSession:
    """Ending a session is otherwise one-way; a mis-clicked End would strand
    the rest of the night in a separate auto-started session."""

    def test_reopens_an_ended_session(self, history):
        session_uuid = history.start_session("Friday Night")
        history.end_session()

        assert history.activate_session(session_uuid) is True
        assert history.get_current_session()["uuid"] == session_uuid

    def test_closes_the_previously_open_session(self, history):
        first = history.start_session("First")
        second = history.start_session("Second")

        history.activate_session(first)

        assert history.get_current_session()["uuid"] == first
        ended = [s for s in history.get_sessions() if s["uuid"] == second][0]
        assert ended["ended_at"] is not None

    def test_never_leaves_two_sessions_open(self, db, history):
        first = history.start_session("First")
        history.start_session("Second")
        history.activate_session(first)

        open_sessions = db.query("SELECT * FROM sessions WHERE ended_at IS NULL")
        assert len(open_sessions) == 1

    def test_new_plays_land_in_the_reactivated_session(self, history, song_id):
        first = history.start_session("First")
        history.record_play(song_id, "Alice")
        history.start_session("Second")
        history.record_play(song_id, "Bob")

        history.activate_session(first)
        history.record_play(song_id, "Dave")

        assert [p["performer"] for p in history.get_plays(first)] == ["Dave", "Alice"]

    def test_activating_the_active_session_keeps_it_active(self, history):
        session_uuid = history.start_session("Friday Night")

        assert history.activate_session(session_uuid) is True
        assert history.get_current_session()["uuid"] == session_uuid

    def test_unknown_session(self, history):
        history.start_session("Friday Night")

        assert history.activate_session("no-such-uuid") is False
        # The open session must survive a failed activate.
        assert history.get_current_session() is not None


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
        assert play["file_path"] == "/songs/a.mp4"

    def test_song_id_may_be_none(self, history):
        history.record_play(None, "Alice")

        play = history.get_plays()[0]
        assert play["file_path"] is None
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


class TestTranspose:
    """Transposing re-queues the same song and ends the stream, but the person
    is still mid-performance. Logging that as a second play would let one
    transpose inflate both Top songs and Top performers."""

    def test_transpose_does_not_log_a_second_play(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "transpose")
        history.record_play(song_id, "Alice")  # restarts in the new key

        assert len(history.get_plays()) == 1
        assert history.get_top_songs()[0]["play_count"] == 1

    def test_transpose_keeps_the_play_resolvable(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "transpose")
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "complete")

        plays = history.get_plays()
        assert len(plays) == 1
        assert plays[0]["completed"] == 1

    def test_transpose_then_skip_is_not_completed(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "transpose")
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "skip")

        assert [p["completed"] for p in history.get_plays()] == [0]

    def test_repeated_transposes_still_log_one_play(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        for _ in range(3):
            events.emit("song_ended", "transpose")
            history.record_play(song_id, "Alice")
        events.emit("song_ended", "complete")

        assert len(history.get_plays()) == 1

    def test_a_different_song_after_a_transpose_is_still_logged(self, db, history, events, song_id):
        """The restart may never arrive; a stale resume must not swallow it."""
        db.insert_songs([{"file_path": "/songs/b.mp4", "youtube_id": None, "format": "mp4"}])
        other = db.get_song_id_by_path("/songs/b.mp4")

        history.record_play(song_id, "Alice")
        events.emit("song_ended", "transpose")
        history.record_play(other, "Bob")

        assert [p["performer"] for p in history.get_plays()] == ["Bob", "Alice"]

    def test_same_song_by_another_performer_is_still_logged(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "transpose")
        history.record_play(song_id, "Bob")

        assert [p["performer"] for p in history.get_plays()] == ["Bob", "Alice"]

    def test_transpose_that_fails_to_restart_resolves_on_the_next_ending(
        self, history, events, song_id
    ):
        """A failed restart emits song_ended(timeout); the play must not stay open."""
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "transpose")
        events.emit("song_ended", "timeout")

        assert history.current_play_id is None
        assert [p["completed"] for p in history.get_plays()] == [0]


class TestLocalTimes:
    """Stored UTC so ordering survives a daylight-saving rollback, but shown as
    wall-clock time. These assertions are only meaningful off UTC, which is
    exactly where the bug showed up."""

    @staticmethod
    def _drift(timestamp: str, reference: datetime) -> float:
        """Seconds between a stored timestamp string and a reference time."""
        parsed = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        return abs((parsed - reference).total_seconds())

    def _seconds_from_now(self, timestamp: str) -> float:
        return self._drift(timestamp, datetime.now())

    def test_stored_as_utc(self, db, history, song_id):
        history.record_play(song_id, "Alice")
        stored = db.query("SELECT played_at FROM plays")[0][0]
        assert self._drift(stored, datetime.now(timezone.utc).replace(tzinfo=None)) < 60

    def test_plays_are_returned_local(self, history, song_id):
        history.record_play(song_id, "Alice")
        assert self._seconds_from_now(history.get_plays()[0]["played_at"]) < 60

    def test_sessions_are_returned_local(self, history):
        history.start_session("Friday Night")
        assert self._seconds_from_now(history.get_sessions()[0]["started_at"]) < 60
        assert self._seconds_from_now(history.get_current_session()["started_at"]) < 60

    def test_ended_at_is_returned_local(self, history):
        history.start_session("Friday Night")
        history.end_session()
        assert self._seconds_from_now(history.get_sessions()[0]["ended_at"]) < 60

    def test_singers_last_played_is_local(self, history, song_id):
        history.record_play(song_id, "Alice")
        assert self._seconds_from_now(history.get_singers()[0]["last_played"]) < 60

    def test_export_is_local(self, history, song_id):
        session_uuid = history.start_session("One")
        history.record_play(song_id, "Alice")
        assert self._seconds_from_now(history.export_plays(session_uuid)[0]["played_at"]) < 60

    def test_ordering_uses_stored_utc(self, db, history, song_id):
        """Ordering must key off the raw column, not the converted one."""
        history.start_session("One")
        for performer in ["First", "Second", "Third"]:
            history.record_play(song_id, performer)
        # Force distinct stored times to prove ORDER BY is on the real column.
        ids = [row[0] for row in db.query("SELECT id FROM plays ORDER BY id")]
        for offset, play_id in enumerate(ids):
            db.execute(
                "UPDATE plays SET played_at = datetime('2026-03-06 10:00:00', ?) WHERE id = ?",
                (f"+{offset} hours", play_id),
            )
        assert [p["performer"] for p in history.get_plays()] == ["Third", "Second", "First"]


class TestCurrentPlayId:
    def test_none_when_nothing_playing(self, history):
        assert history.current_play_id is None

    def test_set_while_playing(self, history, song_id):
        history.record_play(song_id, "Alice")
        assert history.current_play_id == history.get_plays()[0]["id"]

    def test_cleared_once_the_song_ends(self, history, events, song_id):
        history.record_play(song_id, "Alice")
        events.emit("song_ended", "complete")
        assert history.current_play_id is None


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

    def test_scoped_to_a_session(self, history, song_id):
        first = history.start_session("One")
        history.record_play(song_id, "Alice")
        second = history.start_session("Two")
        history.record_play(song_id, "Bob")
        history.record_play(song_id, "Bob")

        assert [s["performer"] for s in history.get_singers(first)] == ["Alice"]
        assert [(s["performer"], s["play_count"]) for s in history.get_singers(second)] == [
            ("Bob", 2)
        ]
        # Unscoped still means everyone, which is what the autocomplete uses.
        assert {s["performer"] for s in history.get_singers()} == {"Alice", "Bob"}

    def test_scoped_to_a_session_with_no_plays(self, history):
        session_uuid = history.start_session("Quiet Night")
        assert history.get_singers(session_uuid) == []

    def test_scoped_groups_casing_variants(self, history, song_id):
        session_uuid = history.start_session("One")
        history.record_play(song_id, "Mike")
        history.record_play(song_id, "mike")

        singers = history.get_singers(session_uuid)
        assert len(singers) == 1
        assert singers[0]["play_count"] == 2


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


class TestPlaySorting:
    """The log is paged, so sorting must happen in SQL. The sort key comes from
    the client and is interpolated, so it has to be whitelisted."""

    @pytest.fixture
    def plays(self, db, history):
        db.insert_songs(
            [
                {"file_path": "/songs/Zebra.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/apple.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        zebra = db.get_song_id_by_path("/songs/Zebra.mp4")
        apple = db.get_song_id_by_path("/songs/apple.mp4")
        history.record_play(zebra, "carol")
        history.record_play(apple, "Alice")
        history.record_play(zebra, "Bob")
        # Distinct stored times so date order is unambiguous.
        for offset, play_id in enumerate(
            r[0] for r in db.query("SELECT id FROM plays ORDER BY id")
        ):
            db.execute(
                "UPDATE plays SET played_at = datetime('2026-03-06 10:00:00', ?) WHERE id = ?",
                (f"+{offset} hours", play_id),
            )

    def test_defaults_to_newest_first(self, history, plays):
        assert [p["performer"] for p in history.get_plays()] == ["Bob", "Alice", "carol"]

    def test_by_date_ascending(self, history, plays):
        assert [p["performer"] for p in history.get_plays(sort="played_at", direction="asc")] == [
            "carol",
            "Alice",
            "Bob",
        ]

    def test_by_performer_is_case_insensitive(self, history, plays):
        assert [p["performer"] for p in history.get_plays(sort="performer", direction="asc")] == [
            "Alice",
            "Bob",
            "carol",
        ]

    def test_by_performer_descending(self, history, plays):
        assert [p["performer"] for p in history.get_plays(sort="performer", direction="desc")] == [
            "carol",
            "Bob",
            "Alice",
        ]

    def test_by_song_is_case_insensitive(self, history, plays):
        """apple must precede Zebra; a case-sensitive sort would put Z first."""
        assert [p["file_path"] for p in history.get_plays(sort="song", direction="asc")] == [
            "/songs/apple.mp4",
            "/songs/Zebra.mp4",
            "/songs/Zebra.mp4",
        ]

    def test_sorting_survives_paging(self, history, plays):
        first = history.get_plays(sort="performer", direction="asc", limit=2, offset=0)
        second = history.get_plays(sort="performer", direction="asc", limit=2, offset=2)
        assert [p["performer"] for p in first + second] == ["Alice", "Bob", "carol"]

    def test_unknown_sort_falls_back_to_date(self, history, plays):
        assert [p["performer"] for p in history.get_plays(sort="nonsense")] == [
            "Bob",
            "Alice",
            "carol",
        ]

    def test_sort_key_cannot_inject_sql(self, history, plays):
        """An unwhitelisted key must never reach the query."""
        injected = "p.played_at; DROP TABLE plays"
        assert len(history.get_plays(sort=injected)) == 3
        assert history.count_plays() == 3


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
