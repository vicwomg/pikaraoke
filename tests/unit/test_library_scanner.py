"""Unit tests for LibraryScanner."""

import os

import pytest

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.library_scanner import (
    LibraryScanner,
    _extract_youtube_id,
    build_song_record,
)


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def scanner(db):
    return LibraryScanner(db)


def _make_song(directory, name="Song---dQw4w9WgXcQ.mp4"):
    path = directory / name
    path.write_text("fake")
    return path


def _seed_songs(directory, count=10):
    """Create numbered song files and return their paths."""
    songs = []
    for i in range(count):
        s = directory / f"Song{i}---{'a' * 10}{i}.mp4"
        s.write_text("fake")
        songs.append(s)
    return songs


class TestScanEmptyDirectory:
    def test_empty_dir_returns_zero_counts(self, scanner, tmp_path):
        result = scanner.scan(str(tmp_path))
        assert result.added == 0
        assert result.moved == 0
        assert result.deleted == 0
        assert result.circuit_tripped is False


class TestScanAddsFiles:
    def test_new_files_are_added(self, scanner, db, tmp_path):
        _make_song(tmp_path, "SongA---aaaaaaaaaaa.mp4")
        _make_song(tmp_path, "SongB---bbbbbbbbbbb.mp3")
        result = scanner.scan(str(tmp_path))
        assert result.added == 2
        assert db.get_song_count() == 2

    def test_existing_files_not_re_added(self, scanner, db, tmp_path):
        _make_song(tmp_path)
        scanner.scan(str(tmp_path))
        result = scanner.scan(str(tmp_path))
        assert result.added == 0
        assert db.get_song_count() == 1

    def test_ignores_non_song_files(self, scanner, db, tmp_path):
        (tmp_path / "readme.txt").write_text("not a song")
        result = scanner.scan(str(tmp_path))
        assert result.added == 0

    def test_scans_subdirectories(self, scanner, db, tmp_path):
        subdir = tmp_path / "artist"
        subdir.mkdir()
        _make_song(subdir, "Song---aaaaaaaaaaa.mp4")
        result = scanner.scan(str(tmp_path))
        assert result.added == 1


class TestScanDeletesFiles:
    def test_deleted_file_is_removed_from_db(self, scanner, db, tmp_path):
        # Use 3 songs so deleting 1 (33%) stays below the 50% circuit-breaker threshold
        songs = [_make_song(tmp_path, f"Song{i}---{'a' * 10}{i}.mp4") for i in range(3)]
        scanner.scan(str(tmp_path))
        songs[0].unlink()
        result = scanner.scan(str(tmp_path))
        assert result.deleted == 1
        assert db.get_song_count() == 2


class TestMoveDetection:
    def test_unambiguous_move_updates_path(self, scanner, db, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        song = old_dir / "Song---dQw4w9WgXcQ.mp4"
        song.write_text("fake")
        scanner.scan(str(tmp_path))

        # Move the file
        song.rename(new_dir / "Song---dQw4w9WgXcQ.mp4")
        result = scanner.scan(str(tmp_path))

        assert result.moved == 1
        assert result.added == 0
        assert result.deleted == 0
        paths = db.get_all_song_paths()
        assert len(paths) == 1
        assert "new" in paths[0]

    def test_ambiguous_move_treated_as_delete_and_insert(self, scanner, db, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        # Seed 3 songs so that deleting 1 (33%) stays below the 50% threshold
        for i in range(2):
            (tmp_path / f"Stable{i}---{'s' * 10}{i}.mp4").write_text("fake")
        (dir_a / "Song---dQw4w9WgXcQ.mp4").write_text("fake")
        scanner.scan(str(tmp_path))

        # Remove from a, add in both b and root (2 candidates -> ambiguous)
        (dir_a / "Song---dQw4w9WgXcQ.mp4").unlink()
        (dir_b / "Song---dQw4w9WgXcQ.mp4").write_text("fake")
        (tmp_path / "Song---dQw4w9WgXcQ.mp4").write_text("fake")

        result = scanner.scan(str(tmp_path))
        # Multiple candidates -> delete + insert, no move
        assert result.moved == 0
        assert result.added == 2
        assert result.deleted == 1

    def test_duplicate_old_basename_no_move(self, scanner, db, tmp_path):
        """Two old paths with the same basename and one new path: no move match."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_c = tmp_path / "c"
        dir_a.mkdir()
        dir_b.mkdir()
        dir_c.mkdir()
        # Seed 3 stable songs to stay below circuit breaker
        for i in range(3):
            (tmp_path / f"Stable{i}---{'s' * 10}{i}.mp4").write_text("fake")
        (dir_a / "Song---dQw4w9WgXcQ.mp4").write_text("fake")
        (dir_b / "Song---dQw4w9WgXcQ.mp4").write_text("fake")
        scanner.scan(str(tmp_path))
        assert db.get_song_count() == 5

        # Remove both, add one new with same basename
        (dir_a / "Song---dQw4w9WgXcQ.mp4").unlink()
        (dir_b / "Song---dQw4w9WgXcQ.mp4").unlink()
        (dir_c / "Song---dQw4w9WgXcQ.mp4").write_text("fake")

        result = scanner.scan(str(tmp_path))
        # Ambiguous old side -> no move, treated as 2 deletes + 1 insert
        assert result.moved == 0
        assert result.deleted == 2
        assert result.added == 1


class TestCircuitBreaker:
    def test_trips_when_over_half_missing(self, scanner, db, tmp_path):
        songs = _seed_songs(tmp_path)
        scanner.scan(str(tmp_path))

        # Delete 6 out of 10 (60% > 50% threshold)
        for s in songs[:6]:
            s.unlink()

        result = scanner.scan(str(tmp_path))
        assert result.circuit_tripped is True
        assert result.deleted == 0
        assert db.get_song_count() == 10  # nothing deleted

    def test_does_not_trip_below_threshold(self, scanner, db, tmp_path):
        songs = _seed_songs(tmp_path)
        scanner.scan(str(tmp_path))

        # Delete 4 out of 10 (40% < 50% threshold)
        for s in songs[:4]:
            s.unlink()

        result = scanner.scan(str(tmp_path))
        assert result.circuit_tripped is False
        assert result.deleted == 4
        assert db.get_song_count() == 6

    def test_adds_still_proceed_when_tripped(self, scanner, db, tmp_path):
        songs = _seed_songs(tmp_path)
        scanner.scan(str(tmp_path))

        # Delete 6 (trips circuit), and add 2 new
        for s in songs[:6]:
            s.unlink()
        (tmp_path / "NewA---zzzzzzzzzzz.mp4").write_text("fake")
        (tmp_path / "NewB---yyyyyyyyyyy.mp4").write_text("fake")

        result = scanner.scan(str(tmp_path))
        assert result.circuit_tripped is True
        assert result.added == 2
        assert result.deleted == 0

    def test_moved_songs_do_not_trip_breaker(self, scanner, db, tmp_path):
        """All songs relocated to a subdirectory should not trip the breaker."""
        songs = _seed_songs(tmp_path)
        scanner.scan(str(tmp_path))

        # Move all songs to a subdirectory (100% of paths change)
        subdir = tmp_path / "moved"
        subdir.mkdir()
        for s in songs:
            s.rename(subdir / s.name)

        result = scanner.scan(str(tmp_path))
        assert result.circuit_tripped is False
        assert result.moved == 10
        assert result.deleted == 0

    def test_no_trip_when_db_empty(self, scanner, db, tmp_path):
        result = scanner.scan(str(tmp_path))
        assert result.circuit_tripped is False


class TestDirectoryChange:
    def test_bypasses_circuit_breaker_on_directory_change(self, scanner, db, tmp_path):
        """When scan directory changes, all old songs should be deleted."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        _seed_songs(old_dir)
        scanner.scan(str(old_dir))
        assert db.get_song_count() == 10

        # Use distinct basenames so move detection doesn't match old songs
        for i in range(2):
            (new_dir / f"NewSong{i}---{'b' * 10}{i}.mp4").write_text("fake")
        result = scanner.scan(str(new_dir))

        assert result.circuit_tripped is False
        assert result.deleted == 10
        assert result.added == 2
        assert db.get_song_count() == 2

    def test_circuit_breaker_still_active_when_same_directory(self, scanner, db, tmp_path):
        """When scanning the same directory, circuit breaker should still work."""
        songs = _seed_songs(tmp_path)
        scanner.scan(str(tmp_path))

        # Delete 6 out of 10 (60% > 50% threshold)
        for s in songs[:6]:
            s.unlink()

        result = scanner.scan(str(tmp_path))
        assert result.circuit_tripped is True
        assert result.deleted == 0
        assert db.get_song_count() == 10

    def test_move_detection_across_directory_change(self, scanner, db, tmp_path):
        """Songs with matching basenames should be detected as moves."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        _seed_songs(old_dir, count=3)
        scanner.scan(str(old_dir))

        # Move one song to new dir, leave others behind
        (old_dir / "Song0---aaaaaaaaaa0.mp4").rename(new_dir / "Song0---aaaaaaaaaa0.mp4")

        result = scanner.scan(str(new_dir))
        assert result.moved == 1

    def test_persists_scan_directory(self, scanner, db, tmp_path):
        """Scan directory should be stored in DB metadata after scan."""
        scanner.scan(str(tmp_path))
        assert db.get_metadata(LibraryScanner._METADATA_KEY) == str(tmp_path)

    def test_bypasses_breaker_when_no_metadata_and_paths_outside_scan_dir(
        self, scanner, db, tmp_path
    ):
        """First scan after upgrade: no metadata stored yet, but DB has songs
        from a different directory. Should bypass the circuit breaker."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        # Seed songs directly into DB (simulating pre-upgrade state with no metadata)
        records = [
            {
                "file_path": str(old_dir / f"Song{i}---{'a' * 10}{i}.mp4"),
                "youtube_id": None,
                "format": "mp4",
            }
            for i in range(10)
        ]
        db.insert_songs(records)
        assert db.get_song_count() == 10

        # Scan new directory — no metadata exists, all DB paths are outside new_dir
        for i in range(2):
            (new_dir / f"NewSong{i}---{'b' * 10}{i}.mp4").write_text("fake")

        result = scanner.scan(str(new_dir))

        assert result.circuit_tripped is False
        assert result.deleted == 10
        assert result.added == 2
        assert db.get_song_count() == 2


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

    def test_cdg_uppercase_detected(self, tmp_path):
        mp3 = tmp_path / "Track---abc1234567x.mp3"
        cdg = tmp_path / "Track---abc1234567x.CDG"
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

    def test_ass_uppercase_detected(self, tmp_path):
        mp4 = tmp_path / "Song---abc1234567x.mp4"
        ass = tmp_path / "Song---abc1234567x.ASS"
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

    def test_uses_cached_files_in_dir(self, tmp_path):
        mp3 = tmp_path / "Track.mp3"
        mp3.touch()
        # Pass a fake directory listing with a .cdg companion
        record = build_song_record(str(mp3), files_in_dir={"Track.cdg", "Track.mp3"})
        assert record["format"] == "cdg"


class TestIntegrityCheck:
    """Startup integrity walk: validate registered artifacts and queue songs
    that need a fresh lyrics pipeline run."""

    def test_first_scan_baselines_ass_sha(self, scanner, db, tmp_path):
        """A freshly-scanned ass_auto file gets its sha recorded; no reprocess."""
        mp4 = _make_song(tmp_path, "Song---aaaaaaaaaaa.mp4")
        ass = tmp_path / "Song---aaaaaaaaaaa.ass"
        ass.write_text("line lyrics")
        # Inject the ass_auto role manually — build_song_record only flags the
        # primary format, the explicit role comes from the lyrics pipeline.
        result = scanner.scan(str(tmp_path))
        sid = db.get_song_id_by_path(str(mp4))
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])
        # Re-scan now that the artifact row exists.
        result = scanner.scan(str(tmp_path))

        rows = [a for a in db.get_artifacts(sid) if a["path"] == str(ass)]
        assert rows[0]["sha256"] is not None
        assert rows[0]["size"] == len("line lyrics")
        # Baselining alone must not flag the song for reprocess.
        assert str(mp4) not in result.reprocess_paths

    def test_unchanged_ass_does_not_reprocess(self, scanner, db, tmp_path):
        """stat-match (same mtime+size) skips sha recompute and reprocess."""
        mp4 = _make_song(tmp_path, "Song---aaaaaaaaaaa.mp4")
        ass = tmp_path / "Song---aaaaaaaaaaa.ass"
        ass.write_text("line lyrics")
        scanner.scan(str(tmp_path))
        sid = db.get_song_id_by_path(str(mp4))
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])
        scanner.scan(str(tmp_path))  # baseline

        result = scanner.scan(str(tmp_path))  # second pass, no FS changes
        assert str(mp4) not in result.reprocess_paths

    def test_ass_content_change_invalidates_and_queues(self, scanner, db, tmp_path):
        """sha mismatch on ass_auto unlinks the file, drops the row, queues reprocess."""
        mp4 = _make_song(tmp_path, "Song---aaaaaaaaaaa.mp4")
        ass = tmp_path / "Song---aaaaaaaaaaa.ass"
        ass.write_text("original content")
        scanner.scan(str(tmp_path))
        sid = db.get_song_id_by_path(str(mp4))
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])
        # Stamp a stale fingerprint so the mismatch path triggers; bypasses
        # the "no recorded sha -> baseline" branch.
        st = ass.stat()
        db.update_artifact_fingerprint(sid, str(ass), st.st_mtime, st.st_size, "0" * 64)
        # Mutate content + bump mtime so the cheap stat-check fails.
        ass.write_text("a wholly different .ass payload, different size")
        future = os.path.getmtime(str(ass)) + 5
        os.utime(str(ass), (future, future))

        result = scanner.scan(str(tmp_path))

        assert str(mp4) in result.reprocess_paths
        assert not ass.exists(), "invalidate_auto_ass should unlink the file"
        rows = [a for a in db.get_artifacts(sid) if a["role"] == "ass_auto"]
        assert rows == []
        # lyrics_sha must clear so the next pipeline run treats LRC as never-fetched.
        assert db.get_song_by_id(sid)["lyrics_sha"] is None

    def test_missing_ass_drops_row_and_queues(self, scanner, db, tmp_path):
        """An ass_auto registered but missing on disk: row dropped + queued."""
        mp4 = _make_song(tmp_path, "Song---aaaaaaaaaaa.mp4")
        scanner.scan(str(tmp_path))
        sid = db.get_song_id_by_path(str(mp4))
        ghost = tmp_path / "Song---aaaaaaaaaaa.ass"
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ghost)}])

        result = scanner.scan(str(tmp_path))

        assert str(mp4) in result.reprocess_paths
        assert [a for a in db.get_artifacts(sid) if a["role"] == "ass_auto"] == []

    def test_missing_cosmetic_artifact_drops_row_only(self, scanner, db, tmp_path):
        """cover_art / vtt / info_json are cosmetic: drop the row, skip reprocess."""
        mp4 = _make_song(tmp_path, "Song---aaaaaaaaaaa.mp4")
        # Cover the "fresh import" case so the song would otherwise be queued.
        # Add a user-authored .ass to short-circuit the no-lyrics check.
        ass = tmp_path / "Song---aaaaaaaaaaa.ass"
        ass.write_text("Title: Aegisub user file\n")
        scanner.scan(str(tmp_path))
        sid = db.get_song_id_by_path(str(mp4))
        db.upsert_artifacts(
            sid,
            [
                {"role": "ass_user", "path": str(ass)},
                {"role": "cover_art", "path": str(tmp_path / "Song---aaaaaaaaaaa.cover.jpg")},
            ],
        )

        result = scanner.scan(str(tmp_path))

        assert str(mp4) not in result.reprocess_paths
        assert [a for a in db.get_artifacts(sid) if a["role"] == "cover_art"] == []
        assert [a for a in db.get_artifacts(sid) if a["role"] == "ass_user"] != []

    def test_imported_song_without_ass_is_queued(self, scanner, db, tmp_path):
        """Fresh scanner-imported mp4 with no .ass companion gets queued."""
        mp4 = _make_song(tmp_path, "Imported---bbbbbbbbbbb.mp4")
        result = scanner.scan(str(tmp_path))
        assert str(mp4) in result.reprocess_paths

    def test_imported_song_with_user_ass_is_not_queued(self, scanner, db, tmp_path):
        """User-supplied .ass means the user owns lyrics — don't queue."""
        mp4 = _make_song(tmp_path, "Song---aaaaaaaaaaa.mp4")
        ass = tmp_path / "Song---aaaaaaaaaaa.ass"
        ass.write_text("Title: Aegisub user file\n")
        scanner.scan(str(tmp_path))
        sid = db.get_song_id_by_path(str(mp4))
        db.upsert_artifacts(sid, [{"role": "ass_user", "path": str(ass)}])

        result = scanner.scan(str(tmp_path))
        assert str(mp4) not in result.reprocess_paths

    def test_cdg_format_song_not_queued(self, scanner, db, tmp_path):
        """CDG songs don't use auto-lyrics; skip the no-lyrics reprocess heuristic."""
        mp3 = _make_song(tmp_path, "Track---ccccccccccc.mp3")
        cdg = tmp_path / "Track---ccccccccccc.cdg"
        cdg.write_text("graphic karaoke")
        result = scanner.scan(str(tmp_path))
        assert str(mp3) not in result.reprocess_paths


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
