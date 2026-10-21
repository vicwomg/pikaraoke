"""Unit tests for SingerManager."""

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.singer_manager import SingerManager


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def singers(db):
    return SingerManager(db, EventSystem())


def _performer(db, singer_id):
    return db.query("SELECT name FROM singers WHERE id = ?", (singer_id,))[0]["name"]


class TestGetOrCreate:
    def test_creates_a_row_on_first_use(self, db, singers):
        singer_id = singers.get_or_create("Alice")
        assert _performer(db, singer_id) == "Alice"

    def test_same_name_returns_the_same_row(self, singers):
        first = singers.get_or_create("Alice")
        assert singers.get_or_create("Alice") == first

    def test_casing_does_not_fork_the_singer(self, singers):
        assert singers.get_or_create("mike") == singers.get_or_create("Mike")

    def test_a_later_casing_wins(self, db, singers):
        singer_id = singers.get_or_create("mike")
        singers.get_or_create("Mike")
        assert _performer(db, singer_id) == "Mike"

    def test_a_matching_casing_leaves_the_row_untouched(self, db, singers):
        singer_id = singers.get_or_create("Mike")
        updated_before = db.query("SELECT name FROM singers WHERE id = ?", (singer_id,))
        singers.get_or_create("Mike")
        assert db.query("SELECT name FROM singers WHERE id = ?", (singer_id,)) == updated_before


class TestFind:
    def test_none_for_an_unknown_name(self, singers):
        assert singers.find("Nobody") is None

    def test_returns_the_row_case_insensitively(self, singers):
        singer_id = singers.get_or_create("Alice")
        assert singers.find("ALICE")["id"] == singer_id

    def test_never_exposes_the_secret_hash(self, db, singers):
        singer_id = singers.get_or_create("Alice")
        db.execute("UPDATE singers SET secret_hash = 'salt:hash' WHERE id = ?", (singer_id,))
        assert "secret_hash" not in singers.find("Alice")


class TestBackfill:
    """The one-time upgrade for a history recorded before singers existed.

    Plays are inserted straight into the table (bypassing record_play, which
    now always writes singer_id) to stand in for that older database.
    """

    @pytest.fixture
    def unlinked_plays(self, db):
        """A session whose plays carry a performer string but no singer_id."""
        session_id = db.execute("INSERT INTO sessions (uuid) VALUES ('s1')").lastrowid

        def _play(performer):
            db.execute(
                "INSERT INTO plays (session_id, song_title, performer) VALUES (?, 'A Song', ?)",
                (session_id, performer),
            )

        return _play

    def test_every_play_gains_a_singer(self, db, singers, unlinked_plays):
        unlinked_plays("Alice")
        unlinked_plays("Bob")
        singers.backfill()
        unlinked = db.query("SELECT COUNT(*) FROM plays WHERE singer_id IS NULL")[0][0]
        assert unlinked == 0

    def test_one_singer_per_name_in_the_latest_casing(self, db, singers, unlinked_plays):
        unlinked_plays("mike")
        unlinked_plays("Mike")
        singers.backfill()
        names = [row["name"] for row in db.query("SELECT name FROM singers")]
        assert names == ["Mike"]

    def test_plays_of_one_name_share_a_singer(self, db, singers, unlinked_plays):
        unlinked_plays("mike")
        unlinked_plays("Mike")
        singers.backfill()
        ids = {row["singer_id"] for row in db.query("SELECT singer_id FROM plays")}
        assert len(ids) == 1

    def test_a_second_run_changes_nothing(self, db, singers, unlinked_plays):
        unlinked_plays("Alice")
        singers.backfill()
        before = db.query("SELECT id, name FROM singers")
        singers.backfill()
        assert db.query("SELECT id, name FROM singers") == before

    def test_empty_history_is_a_no_op(self, db, singers):
        singers.backfill()
        assert db.query("SELECT COUNT(*) FROM singers")[0][0] == 0
