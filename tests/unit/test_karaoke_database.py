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
        assert ver == 2

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


def _insert_song(db, path="/songs/test.mp4"):
    db.insert_songs([{"file_path": path, "youtube_id": None, "format": "mp4"}])
    return db.get_song_id_by_path(path)


class TestArtifacts:
    def test_upsert_and_get(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(
            sid,
            [
                {"role": "primary_media", "path": "/songs/test.mp4"},
                {"role": "audio_source", "path": "/songs/test.m4a"},
            ],
        )
        rows = db.get_artifacts(sid)
        assert {(r["role"], r["path"]) for r in rows} == {
            ("primary_media", "/songs/test.mp4"),
            ("audio_source", "/songs/test.m4a"),
        }

    def test_upsert_replaces_role_for_same_path(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": "/songs/test.ass"}])
        db.upsert_artifacts(sid, [{"role": "ass_user", "path": "/songs/test.ass"}])
        rows = db.get_artifacts(sid)
        assert len(rows) == 1
        assert rows[0]["role"] == "ass_user"

    def test_delete_artifact(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(sid, [{"role": "vtt", "path": "/songs/test.en.vtt"}])
        db.delete_artifact(sid, "/songs/test.en.vtt")
        assert db.get_artifacts(sid) == []

    def test_delete_artifacts_by_role(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(
            sid,
            [
                {"role": "vtt", "path": "/songs/test.en.vtt"},
                {"role": "vtt", "path": "/songs/test.pl.vtt"},
                {"role": "primary_media", "path": "/songs/test.mp4"},
            ],
        )
        db.delete_artifacts_by_role(sid, "vtt")
        roles = {r["role"] for r in db.get_artifacts(sid)}
        assert roles == {"primary_media"}

    def test_cascade_deletes_artifacts_when_song_deleted(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": "/songs/test.ass"}])
        db.delete_by_path("/songs/test.mp4")
        # Artifact rows should cascade away.
        rows = db._conn.execute("SELECT * FROM song_artifacts").fetchall()
        assert rows == []

    def test_replace_artifacts_swaps_set(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(
            sid,
            [
                {"role": "primary_media", "path": "/old/test.mp4"},
                {"role": "audio_source", "path": "/old/test.m4a"},
            ],
        )
        db.replace_artifacts(
            sid,
            [
                {"role": "primary_media", "path": "/new/test.mp4"},
                {"role": "cdg", "path": "/new/test.cdg"},
            ],
        )
        rows = db.get_artifacts(sid)
        assert {(r["role"], r["path"]) for r in rows} == {
            ("primary_media", "/new/test.mp4"),
            ("cdg", "/new/test.cdg"),
        }


class TestAudioFingerprint:
    def test_update_and_read_back(self, db):
        sid = _insert_song(db)
        db.update_audio_fingerprint(sid, mtime=1234.5, size=999, sha256="a" * 64)
        row = db.get_song_by_id(sid)
        assert row["audio_mtime"] == 1234.5
        assert row["audio_size"] == 999
        assert row["audio_sha256"] == "a" * 64

    def test_count_songs_by_sha256(self, db):
        s1 = _insert_song(db, "/songs/a.mp4")
        s2 = _insert_song(db, "/songs/b.mp4")
        _insert_song(db, "/songs/c.mp4")
        sha = "deadbeef" * 8
        db.update_audio_fingerprint(s1, 1.0, 1, sha)
        db.update_audio_fingerprint(s2, 2.0, 2, sha)
        # c has no fingerprint
        assert db.count_songs_by_sha256(sha) == 2
        assert db.count_songs_by_sha256("x" * 64) == 0

    def test_clear_audio_fingerprint(self, db):
        sid = _insert_song(db)
        db.update_audio_fingerprint(sid, 1.0, 1, "a" * 64)
        db.clear_audio_fingerprint(sid)
        row = db.get_song_by_id(sid)
        assert row["audio_mtime"] is None
        assert row["audio_sha256"] is None


class TestProcessingConfig:
    def test_update_demucs_model(self, db):
        sid = _insert_song(db)
        db.update_processing_config(sid, demucs_model="htdemucs")
        assert db.get_song_by_id(sid)["demucs_model"] == "htdemucs"

    def test_update_lyrics_fields(self, db):
        sid = _insert_song(db)
        db.update_processing_config(sid, aligner_model="whisperx-base", lyrics_source="lrclib")
        row = db.get_song_by_id(sid)
        assert row["aligner_model"] == "whisperx-base"
        assert row["lyrics_source"] == "lrclib"

    def test_skips_none_args(self, db):
        sid = _insert_song(db)
        db.update_processing_config(sid, demucs_model="htdemucs")
        db.update_processing_config(sid, aligner_model=None)
        assert db.get_song_by_id(sid)["demucs_model"] == "htdemucs"


class TestTrackMetadata:
    def test_partial_update_ignores_none(self, db):
        sid = _insert_song(db)
        db.update_track_metadata(sid, duration_seconds=180.5, album=None, itunes_id="12345")
        row = db.get_song_by_id(sid)
        assert row["duration_seconds"] == 180.5
        assert row["album"] is None
        assert row["itunes_id"] == "12345"

    def test_rejects_unknown_field(self, db):
        sid = _insert_song(db)
        with pytest.raises(ValueError):
            db.update_track_metadata(sid, bogus_col="x")


class TestGetSongHelpers:
    def test_get_song_id_by_path(self, db):
        sid = _insert_song(db, "/songs/a.mp4")
        assert db.get_song_id_by_path("/songs/a.mp4") == sid
        assert db.get_song_id_by_path("/songs/missing.mp4") is None

    def test_get_song_by_id(self, db):
        sid = _insert_song(db, "/songs/a.mp4")
        row = db.get_song_by_id(sid)
        assert row["file_path"] == "/songs/a.mp4"
        assert db.get_song_by_id(999999) is None

    def test_get_songs_without_artifacts(self, db):
        a = _insert_song(db, "/songs/a.mp4")
        b = _insert_song(db, "/songs/b.mp4")
        db.upsert_artifacts(a, [{"role": "primary_media", "path": "/songs/a.mp4"}])
        missing = db.get_songs_without_artifacts()
        assert missing == [(b, "/songs/b.mp4")]


class TestMigrationFromV1:
    def test_migrates_v1_db_to_v2(self, tmp_path):
        """Open a fresh DB (which is v2), manually downgrade it to the v1 baseline
        schema, reopen, and assert the migration ran (user_version=2, new columns
        and tables exist).
        """
        db_path = str(tmp_path / "v1.db")
        # Create a v1-shaped DB by hand so we're migrating a realistic legacy state.
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                youtube_id TEXT,
                format TEXT NOT NULL,
                artist TEXT, title TEXT, variant TEXT, year INTEGER, genre TEXT,
                metadata_status TEXT DEFAULT 'pending',
                enrichment_attempts INTEGER DEFAULT 0,
                last_enrichment_attempt TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO songs (file_path, format) VALUES ('/songs/legacy.mp4', 'mp4');
            PRAGMA user_version = 1;
            """
        )
        conn.commit()
        conn.close()

        # Open via KaraokeDatabase: should apply v2 migration in-place.
        db = KaraokeDatabase(db_path)
        try:
            ver = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == 2

            cols = {row[1] for row in db._conn.execute("PRAGMA table_info(songs)").fetchall()}
            assert {
                "audio_sha256",
                "audio_mtime",
                "audio_size",
                "demucs_model",
                "aligner_model",
                "lyrics_source",
                "duration_seconds",
                "source_url",
                "language",
                "album",
                "track_number",
                "release_date",
                "itunes_id",
                "musicbrainz_recording_id",
                "isrc",
            }.issubset(cols)

            tables = {
                r[0]
                for r in db._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "song_artifacts" in tables

            # Legacy row survived.
            assert db.get_song_count() == 1
        finally:
            db.close()

    def test_foreign_keys_enabled(self, db):
        fk = db._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
