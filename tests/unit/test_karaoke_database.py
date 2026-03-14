"""Unit tests for KaraokeDatabase."""

import os

import pytest

from pikaraoke.lib.karaoke_database import (
    KaraokeDatabase,
    _extract_youtube_id,
    build_song_record,
)


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


class TestBuildSongRecord:
    def test_mp4_format(self, tmp_path):
        song = tmp_path / "Song---dQw4w9WgXcQ.mp4"
        song.touch()
        record = build_song_record(str(song))
        assert record["format"] == "mp4"
        assert record["youtube_id"] == "dQw4w9WgXcQ"

    def test_cdg_pair_detected(self, tmp_path):
        mp3 = tmp_path / "Track---abc1234567x.mp3"
        cdg = tmp_path / "Track---abc1234567x.cdg"
        mp3.touch()
        cdg.touch()
        record = build_song_record(str(mp3))
        assert record["format"] == "cdg"

    def test_mp4_ass_pair_detected(self, tmp_path):
        mp4 = tmp_path / "Song---abc1234567x.mp4"
        ass = tmp_path / "Song---abc1234567x.ass"
        mp4.touch()
        ass.touch()
        record = build_song_record(str(mp4))
        assert record["format"] == "ass"

    def test_zip_format(self, tmp_path):
        zf = tmp_path / "Song---abc1234567x.zip"
        zf.touch()
        record = build_song_record(str(zf))
        assert record["format"] == "zip"

    def test_standalone_mp3(self, tmp_path):
        mp3 = tmp_path / "Song.mp3"
        mp3.touch()
        record = build_song_record(str(mp3))
        assert record["format"] == "mp3"


class TestExtractYoutubeId:
    def test_pikaraoke_format(self):
        assert _extract_youtube_id("Song---dQw4w9WgXcQ.mp4") == "dQw4w9WgXcQ"

    def test_ytdlp_format(self):
        assert _extract_youtube_id("Song [dQw4w9WgXcQ].mp4") == "dQw4w9WgXcQ"

    def test_no_id(self):
        assert _extract_youtube_id("Just A Song.mp4") is None

    def test_pikaraoke_preferred_over_ytdlp(self):
        # PiKaraoke format takes priority
        result = _extract_youtube_id("Song [AAAAAAAAAAA]---BBBBBBBBBBB.mp4")
        assert result == "BBBBBBBBBBB"


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
