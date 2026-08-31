"""Unit tests for KaraokeDatabase."""

import os
import sqlite3

import pytest

from pikaraoke.lib.karaoke_database import KaraokeDatabase

# The songs table exactly as shipped by 1.20.0, before play history existed.
_SCHEMA_1_20_0 = """
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    youtube_id TEXT,
    format TEXT NOT NULL,
    artist TEXT,
    title TEXT,
    variant TEXT,
    year INTEGER,
    genre TEXT,
    metadata_status TEXT DEFAULT 'pending',
    enrichment_attempts INTEGER DEFAULT 0,
    last_enrichment_attempt TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Added by the v2 migration.
_NEW_IN_V2 = {
    "suggested_genre",
    "suggested_year",
    "suggested_score",
    "metadata_country",
    "musical_key",
}


_PLAY_HISTORY_PRE_V2 = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    name TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    song_id INTEGER,
    youtube_id TEXT,
    song_title TEXT NOT NULL,
    performer TEXT NOT NULL,
    played_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    completed INTEGER DEFAULT 0
);
"""


def _song_columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(songs)").fetchall()}


def _play_columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(plays)").fetchall()}


@pytest.fixture
def db(tmp_path):
    """A fresh KaraokeDatabase backed by a temporary file."""
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


class TestInit:
    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "pikaraoke.db")
        db = KaraokeDatabase(db_path)
        db.close()
        assert os.path.exists(db_path)

    def test_wal_mode(self, db):
        mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_user_version(self, db):
        ver = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 2

    def test_fresh_schema_has_the_new_v2_columns(self, db):
        assert _NEW_IN_V2 <= _song_columns(db._conn)

    def test_fresh_schema_keeps_the_durable_year_and_genre(self, db):
        assert {"year", "genre"} <= _song_columns(db._conn)

    def test_songs_table_exists(self, db):
        tables = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "songs" in tables

    def test_empty_on_init(self, db):
        assert db.get_song_count() == 0


class TestUpgradeFromExistingDatabase:
    """A 1.20.0 database has only songs+metadata; the new tables must appear
    on open without touching the existing library."""

    @pytest.fixture
    def legacy_db_path(self, tmp_path):
        path = str(tmp_path / "pikaraoke.db")
        conn = sqlite3.connect(path)
        conn.executescript(_SCHEMA_1_20_0)
        conn.execute(
            "INSERT INTO songs (file_path, youtube_id, format) VALUES (?, ?, ?)",
            ("/songs/existing.mp4", "dQw4w9WgXcQ", "mp4"),
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        return path

    def test_creates_new_tables(self, legacy_db_path):
        db = KaraokeDatabase(legacy_db_path)
        tables = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        db.close()
        assert {"sessions", "plays"} <= tables

    def test_preserves_existing_songs(self, legacy_db_path):
        db = KaraokeDatabase(legacy_db_path)
        paths = db.get_all_song_paths()
        db.close()
        assert paths == ["/songs/existing.mp4"]


class TestSchemaV2Migration:
    """CREATE TABLE IF NOT EXISTS cannot add a column to a table that already
    exists, so a 1.20.0 songs table can only gain the staging columns by
    ALTER TABLE."""

    @pytest.fixture
    def legacy_db_path(self, tmp_path):
        path = str(tmp_path / "pikaraoke.db")
        conn = sqlite3.connect(path)
        conn.executescript(_SCHEMA_1_20_0)
        conn.execute(
            "INSERT INTO songs (file_path, youtube_id, format, artist, title) "
            "VALUES (?, ?, ?, ?, ?)",
            ("/songs/existing.mp4", "dQw4w9WgXcQ", "mp4", "Beyonce", "Halo"),
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        return path

    def test_v1_database_gains_the_columns(self, legacy_db_path):
        db = KaraokeDatabase(legacy_db_path)
        columns = _song_columns(db._conn)
        db.close()
        assert _NEW_IN_V2 <= columns

    def test_version_is_stamped_to_2(self, legacy_db_path):
        db = KaraokeDatabase(legacy_db_path)
        ver = db._conn.execute("PRAGMA user_version").fetchone()[0]
        db.close()
        assert ver == 2

    def test_existing_row_survives_with_nulls_in_the_new_columns(self, legacy_db_path):
        db = KaraokeDatabase(legacy_db_path)
        row = db._conn.execute(
            "SELECT file_path, youtube_id, artist, title, suggested_genre, "
            "suggested_year, suggested_score, metadata_country, musical_key FROM songs"
        ).fetchone()
        db.close()
        assert tuple(row) == ("/songs/existing.mp4", "dQw4w9WgXcQ", "Beyonce", "Halo", *[None] * 5)

    def test_reopening_does_not_re_run_the_migration(self, legacy_db_path):
        # Catches a literal PRAGMA user_version = 1: the second open would
        # re-run ALTER TABLE and die with "duplicate column name".
        for _ in range(3):
            db = KaraokeDatabase(legacy_db_path)
            db.close()

    def test_play_history_tables_are_created_when_absent(self, legacy_db_path):
        db = KaraokeDatabase(legacy_db_path)
        tables = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        db.close()
        assert {"sessions", "plays"} <= tables

    def test_fresh_database_has_the_plays_key_column(self, tmp_path):
        db = KaraokeDatabase(str(tmp_path / "fresh.db"))
        columns = _play_columns(db._conn)
        db.close()
        assert "semitones" in columns

    def test_a_fresh_database_does_not_enter_the_migration(self, tmp_path, monkeypatch):
        # user_version 0 means there is no songs table to ALTER yet.
        def fail(*args):
            raise AssertionError("_migrate ran on a fresh database")

        monkeypatch.setattr(KaraokeDatabase, "_migrate", fail)
        db = KaraokeDatabase(str(tmp_path / "fresh.db"))
        db.close()


class TestSchemaV2PlaysMigration:
    """1.20.0 stamped version 1 before play history existed, so a version 1
    database may or may not have the plays table. The migration has to add the
    key column when it is there and skip it when it is not."""

    @pytest.fixture
    def legacy_db_with_play_history(self, tmp_path):
        path = str(tmp_path / "pikaraoke.db")
        conn = sqlite3.connect(path)
        conn.executescript(_SCHEMA_1_20_0)
        conn.executescript(_PLAY_HISTORY_PRE_V2)
        conn.execute("INSERT INTO sessions (uuid, name) VALUES ('abc', 'Friday')")
        conn.execute(
            "INSERT INTO plays (session_id, song_title, performer) VALUES (1, ?, ?)",
            ("Halo", "Beyonce"),
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        return path

    def test_existing_plays_table_gains_the_key_column(self, legacy_db_with_play_history):
        db = KaraokeDatabase(legacy_db_with_play_history)
        columns = _play_columns(db._conn)
        db.close()
        assert "semitones" in columns

    def test_existing_play_rows_survive_and_default_to_no_shift(self, legacy_db_with_play_history):
        db = KaraokeDatabase(legacy_db_with_play_history)
        row = db._conn.execute("SELECT song_title, performer, semitones FROM plays").fetchone()
        db.close()
        assert tuple(row) == ("Halo", "Beyonce", 0)

    def test_reopening_does_not_re_run_the_migration(self, legacy_db_with_play_history):
        for _ in range(3):
            db = KaraokeDatabase(legacy_db_with_play_history)
            db.close()


class TestGetSongIdentity:
    def test_returns_nones_when_missing(self, db):
        assert db.get_song_identity("/songs/nope.mp4") == (None, None)

    def test_returns_id_for_known_path(self, db):
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        song_id, youtube_id = db.get_song_identity("/songs/a.mp4")
        assert youtube_id is None
        row = db._conn.execute("SELECT file_path FROM songs WHERE id = ?", (song_id,)).fetchone()
        assert row[0] == "/songs/a.mp4"

    def test_returns_the_youtube_id_when_the_song_has_one(self, db):
        """Play history stores this as the identity that outlives the song row."""
        db.insert_songs(
            [{"file_path": "/songs/a.mp4", "youtube_id": "dQw4w9WgXcQ", "format": "mp4"}]
        )
        assert db.get_song_identity("/songs/a.mp4")[1] == "dQw4w9WgXcQ"


class TestForeignKeys:
    """These all fail without the PRAGMA foreign_keys in _connect()."""

    @pytest.fixture
    def play(self, db):
        """A session with one play against one song. Returns (song_id, play_id)."""
        db.insert_songs([{"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"}])
        song_id = db.get_song_identity("/songs/a.mp4")[0]
        session_id = db.execute("INSERT INTO sessions (uuid) VALUES ('s1')").lastrowid
        play_id = db.execute(
            "INSERT INTO plays (session_id, song_id, song_title, performer) "
            "VALUES (?, ?, 'A Song', 'Alice')",
            (session_id, song_id),
        ).lastrowid
        return song_id, play_id

    def test_deleting_a_song_nulls_the_play_song_id(self, db, play):
        _, play_id = play
        db.delete_by_path("/songs/a.mp4")

        row = db.query("SELECT song_id, performer FROM plays WHERE id = ?", (play_id,))[0]
        assert row["song_id"] is None
        assert row["performer"] == "Alice"

    def test_deleting_a_session_cascades_to_plays(self, db, play):
        db.execute("DELETE FROM sessions WHERE uuid = 's1'")
        assert db.query("SELECT * FROM plays") == []

    def test_play_requires_a_real_session(self, db):
        # song_title supplied so this fails on the foreign key, not NOT NULL.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO plays (session_id, song_title, performer) "
                "VALUES (999, 'A Song', 'Alice')"
            )


class TestGetAllSongPaths:
    def test_returns_empty_list_when_no_songs(self, db):
        assert db.get_all_song_paths() == []

    def test_returns_all_inserted_paths(self, db):
        db.insert_songs(
            [
                {"file_path": "/songs/zebra.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/apple.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/Mango.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        paths = set(db.get_all_song_paths())
        assert paths == {"/songs/zebra.mp4", "/songs/apple.mp4", "/songs/Mango.mp4"}


class TestInsertSongs:
    def test_basic_insert(self, db):
        db.insert_songs([{"file_path": "/songs/test.mp4", "youtube_id": None, "format": "mp4"}])
        assert db.get_song_count() == 1

    def test_ignores_duplicate_file_path(self, db):
        record = {"file_path": "/songs/test.mp4", "youtube_id": None, "format": "mp4"}
        db.insert_songs([record])
        db.insert_songs([record])
        assert db.get_song_count() == 1

    def test_batch_insert(self, db):
        records = [
            {"file_path": f"/songs/song{i}.mp4", "youtube_id": None, "format": "mp4"}
            for i in range(10)
        ]
        db.insert_songs(records)
        assert db.get_song_count() == 10

    def test_stores_youtube_id(self, db):
        db.insert_songs(
            [{"file_path": "/songs/t.mp4", "youtube_id": "dQw4w9WgXcQ", "format": "mp4"}]
        )
        row = db._conn.execute("SELECT youtube_id FROM songs").fetchone()
        assert row[0] == "dQw4w9WgXcQ"


class TestDeleteByPath:
    def test_deletes_single_song(self, db):
        db.insert_songs([{"file_path": "/songs/test.mp4", "youtube_id": None, "format": "mp4"}])
        db.delete_by_path("/songs/test.mp4")
        assert db.get_song_count() == 0

    def test_no_error_on_missing_path(self, db):
        db.delete_by_path("/songs/nonexistent.mp4")  # should not raise


class TestDeleteByPaths:
    def test_batch_delete(self, db):
        records = [
            {"file_path": f"/songs/song{i}.mp4", "youtube_id": None, "format": "mp4"}
            for i in range(5)
        ]
        db.insert_songs(records)
        db.delete_by_paths(["/songs/song0.mp4", "/songs/song1.mp4"])
        assert db.get_song_count() == 3


class TestUpdatePath:
    def test_updates_file_path(self, db):
        db.insert_songs([{"file_path": "/songs/old.mp4", "youtube_id": None, "format": "mp4"}])
        db.update_path("/songs/old.mp4", "/songs/new.mp4")
        assert db.get_all_song_paths() == ["/songs/new.mp4"]


class TestUpdatePaths:
    def test_batch_moves(self, db):
        db.insert_songs(
            [
                {"file_path": "/old/a.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/old/b.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        db.update_paths([("/old/a.mp4", "/new/a.mp4"), ("/old/b.mp4", "/new/b.mp4")])
        paths = set(db.get_all_song_paths())
        assert paths == {"/new/a.mp4", "/new/b.mp4"}


class TestMetadata:
    def test_get_returns_none_when_unset(self, db):
        assert db.get_metadata("nonexistent") is None

    def test_set_and_get_round_trip(self, db):
        db.set_metadata("scan_dir", "/songs")
        assert db.get_metadata("scan_dir") == "/songs"

    def test_set_overwrites_existing(self, db):
        db.set_metadata("scan_dir", "/old")
        db.set_metadata("scan_dir", "/new")
        assert db.get_metadata("scan_dir") == "/new"

    def test_metadata_table_exists(self, db):
        tables = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "metadata" in tables


class TestApplyScanDiff:
    def test_applies_moves_inserts_deletes_atomically(self, db):
        db.insert_songs(
            [
                {"file_path": "/songs/old.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/remove.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        db.apply_scan_diff(
            moves=[("/songs/old.mp4", "/songs/new.mp4")],
            inserts=[{"file_path": "/songs/added.mp4", "youtube_id": None, "format": "mp4"}],
            deletes=["/songs/remove.mp4"],
        )
        paths = set(db.get_all_song_paths())
        assert paths == {"/songs/new.mp4", "/songs/added.mp4"}

    def test_rolls_back_on_error(self, db):
        db.insert_songs(
            [
                {"file_path": "/songs/a.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/b.mp4", "youtube_id": None, "format": "mp4"},
                {"file_path": "/songs/c.mp4", "youtube_id": None, "format": "mp4"},
            ]
        )
        # Moving two rows to the same path violates UNIQUE on file_path.
        # The entire transaction (including the delete) should roll back.
        with pytest.raises(Exception):
            db.apply_scan_diff(
                moves=[("/songs/a.mp4", "/songs/clash.mp4"), ("/songs/b.mp4", "/songs/clash.mp4")],
                inserts=[],
                deletes=["/songs/c.mp4"],
            )
        # All 3 original songs should remain untouched
        assert db.get_song_count() == 3
        assert set(db.get_all_song_paths()) == {"/songs/a.mp4", "/songs/b.mp4", "/songs/c.mp4"}


class TestIntegrityCheck:
    def test_ok_on_fresh_db(self, db):
        ok, msg = db.check_integrity()
        assert ok is True
        assert msg == "ok"


class TestUnicodeFilenames:
    def test_unicode_path_stored_and_retrieved(self, db):
        path = "/songs/Céline Dion - My Heart---abc1234567x.mp4"
        db.insert_songs([{"file_path": path, "youtube_id": "abc1234567x", "format": "mp4"}])
        assert db.get_all_song_paths() == [path]


class TestGetPathsByYoutubeIds:
    """Powers the "In library" tag on YouTube search results."""

    @staticmethod
    def _record(youtube_id):
        return {
            "file_path": f"/songs/Song {youtube_id}---{youtube_id}.mp4",
            "youtube_id": youtube_id,
            "format": "mp4",
        }

    def test_empty_input_makes_no_query(self, db):
        assert db.get_paths_by_youtube_ids([]) == {}

    def test_no_match(self, db):
        db.insert_songs([self._record("aaaaaaaaaaa")])
        assert db.get_paths_by_youtube_ids(["zzzzzzzzzzz"]) == {}

    def test_partial_match_returns_only_what_is_present(self, db):
        db.insert_songs([self._record("aaaaaaaaaaa")])
        result = db.get_paths_by_youtube_ids(["aaaaaaaaaaa", "zzzzzzzzzzz"])
        assert result == {"aaaaaaaaaaa": "/songs/Song aaaaaaaaaaa---aaaaaaaaaaa.mp4"}

    def test_multiple_matches(self, db):
        db.insert_songs([self._record("aaaaaaaaaaa"), self._record("bbbbbbbbbbb")])
        result = db.get_paths_by_youtube_ids(["aaaaaaaaaaa", "bbbbbbbbbbb"])
        assert set(result) == {"aaaaaaaaaaa", "bbbbbbbbbbb"}

    def test_ignores_songs_without_a_youtube_id(self, db):
        db.insert_songs([{"file_path": "/songs/Local.zip", "youtube_id": None, "format": "zip"}])
        assert db.get_paths_by_youtube_ids(["aaaaaaaaaaa"]) == {}
