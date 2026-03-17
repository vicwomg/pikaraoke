"""Unit tests for KaraokeDatabase."""

import os

import pytest

from pikaraoke.lib.karaoke_database import KaraokeDatabase


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
        assert ver == 1

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
