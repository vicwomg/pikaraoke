"""Unit tests for LibraryScanner."""

import pytest

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.library_scanner import LibraryScanner


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
