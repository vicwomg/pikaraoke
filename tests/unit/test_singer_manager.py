"""Unit tests for SingerManager."""

import os

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.singer_manager import SingerManager


def _display_name(path):
    """Stand in for SongManager.display_name_from_path: stable title per path."""
    return os.path.splitext(os.path.basename(path))[0]


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def events():
    return EventSystem()


@pytest.fixture
def singers(db, events):
    return SingerManager(db, events, _display_name)


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


class TestFavourites:
    @pytest.fixture
    def library(self, db):
        """A YouTube song (durable id) and a local rip (no id), both in library."""
        db.insert_songs(
            [
                {
                    "file_path": "/s/Halo---dQw4w9WgXcQ.mp4",
                    "youtube_id": "dQw4w9WgXcQ",
                    "format": "mp4",
                },
                {"file_path": "/s/Local Rip.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        return {"youtube": "/s/Halo---dQw4w9WgXcQ.mp4", "local": "/s/Local Rip.mp4"}

    def test_starring_lists_the_song(self, singers, library):
        singers.add_favourite("Alice", library["youtube"])
        assert singers.get_favourite_paths("Alice") == {library["youtube"]}

    def test_an_unnamed_or_unknown_singer_has_none(self, singers):
        assert singers.get_favourite_paths("Nobody") == set()

    def test_starring_twice_leaves_one_row(self, db, singers, library):
        singers.add_favourite("Alice", library["youtube"])
        singers.add_favourite("Alice", library["youtube"])
        assert db.query("SELECT COUNT(*) FROM singer_songs")[0][0] == 1

    def test_unstarring_removes_the_song(self, singers, library):
        singers.add_favourite("Alice", library["youtube"])
        singers.remove_favourite("Alice", library["youtube"])
        assert singers.get_favourite_paths("Alice") == set()

    def test_unstarring_a_song_never_starred_creates_no_singer(self, singers, library):
        singers.remove_favourite("Alice", library["youtube"])
        assert singers.find("Alice") is None

    def test_favourites_are_per_singer(self, singers, library):
        singers.add_favourite("Alice", library["youtube"])
        assert singers.get_favourite_paths("Bob") == set()

    def test_a_local_rip_is_favourited_by_its_live_link(self, singers, library):
        singers.add_favourite("Alice", library["local"])
        assert singers.get_favourite_paths("Alice") == {library["local"]}

    def test_a_youtube_favourite_follows_a_rename(self, db, singers, events, library):
        singers.add_favourite("Alice", library["youtube"])
        song_id = db.get_song_identity(library["youtube"])[0]
        new_path = "/s/Halo (Live)---dQw4w9WgXcQ.mp4"
        db.update_path(library["youtube"], new_path)
        events.emit("song_renamed", song_id, "Halo (Live)")
        assert singers.get_favourite_paths("Alice") == {new_path}

    def test_a_youtube_favourite_returns_after_redownload(self, db, singers, library):
        singers.add_favourite("Alice", library["youtube"])
        db.delete_by_path(library["youtube"])
        assert singers.get_favourite_paths("Alice") == set()
        db.insert_songs(
            [
                {
                    "file_path": "/s/Halo again---dQw4w9WgXcQ.mp4",
                    "youtube_id": "dQw4w9WgXcQ",
                    "format": "mp4",
                }
            ]
        )
        assert singers.get_favourite_paths("Alice") == {"/s/Halo again---dQw4w9WgXcQ.mp4"}

    def test_a_renamed_local_rip_unstars_by_its_new_name(self, db, singers, events, library):
        """The song_renamed subscription keeps the title (a local rip's identity)
        current, so the star is still reachable after the file is renamed."""
        singers.add_favourite("Alice", library["local"])
        song_id = db.get_song_identity(library["local"])[0]
        new_path = "/s/Local Rip Renamed.mp4"
        db.update_path(library["local"], new_path)
        events.emit("song_renamed", song_id, _display_name(new_path))
        singers.remove_favourite("Alice", new_path)
        assert singers.get_favourite_paths("Alice") == set()
