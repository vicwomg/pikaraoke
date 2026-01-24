"""Unit tests for SongList class."""


import pytest

from pikaraoke.lib.song_list import SongList


class TestSongListBasicOperations:
    """Tests for basic SongList operations."""

    def test_add_song(self):
        """Test adding a song to the list."""
        sl = SongList()
        sl.add("/songs/test.mp4")
        assert "/songs/test.mp4" in sl
        assert len(sl) == 1

    def test_add_duplicate_song(self):
        """Test that adding duplicate doesn't create duplicates."""
        sl = SongList()
        sl.add("/songs/test.mp4")
        sl.add("/songs/test.mp4")
        assert len(sl) == 1

    def test_remove_song(self):
        """Test removing a song from the list."""
        sl = SongList()
        sl.add("/songs/test.mp4")
        sl.remove("/songs/test.mp4")
        assert "/songs/test.mp4" not in sl
        assert len(sl) == 0

    def test_remove_nonexistent_song_logs_warning(self, caplog):
        """Test that removing nonexistent song logs warning."""
        sl = SongList()
        sl.remove("/songs/nonexistent.mp4")
        assert "not found" in caplog.text.lower()

    def test_discard_song(self):
        """Test discarding a song (no error if not present)."""
        sl = SongList()
        sl.add("/songs/test.mp4")
        sl.discard("/songs/test.mp4")
        assert "/songs/test.mp4" not in sl

    def test_discard_nonexistent_no_error(self):
        """Test that discarding nonexistent song doesn't raise error."""
        sl = SongList()
        sl.discard("/songs/nonexistent.mp4")  # Should not raise

    def test_clear(self):
        """Test clearing all songs."""
        sl = SongList()
        sl.add("/songs/song1.mp4")
        sl.add("/songs/song2.mp4")
        sl.clear()
        assert len(sl) == 0

    def test_update(self):
        """Test replacing all songs with a new list."""
        sl = SongList()
        sl.add("/songs/old.mp4")
        sl.update(["/songs/new1.mp4", "/songs/new2.mp4"])
        assert "/songs/old.mp4" not in sl
        assert "/songs/new1.mp4" in sl
        assert "/songs/new2.mp4" in sl
        assert len(sl) == 2


class TestSongListValidation:
    """Tests for SongList validation methods."""

    def test_is_valid_song_mp4(self, tmp_path):
        """Test that .mp4 files are valid."""
        sl = SongList()
        song_file = tmp_path / "song.mp4"
        song_file.touch()
        assert sl.is_valid_song(str(song_file)) is True

    def test_is_valid_song_mp3(self, tmp_path):
        """Test that .mp3 files are valid."""
        sl = SongList()
        song_file = tmp_path / "song.mp3"
        song_file.touch()
        assert sl.is_valid_song(str(song_file)) is True

    def test_is_valid_song_zip(self, tmp_path):
        """Test that .zip files are valid (CDG format)."""
        sl = SongList()
        song_file = tmp_path / "song.zip"
        song_file.touch()
        assert sl.is_valid_song(str(song_file)) is True

    def test_is_valid_song_webm(self, tmp_path):
        """Test that .webm files are valid."""
        sl = SongList()
        song_file = tmp_path / "song.webm"
        song_file.touch()
        assert sl.is_valid_song(str(song_file)) is True

    def test_is_valid_song_invalid_extension(self, tmp_path):
        """Test that unsupported extensions are invalid."""
        sl = SongList()
        song_file = tmp_path / "song.txt"
        song_file.touch()
        assert sl.is_valid_song(str(song_file)) is False

    def test_is_valid_song_nonexistent_file(self):
        """Test that nonexistent files are invalid."""
        sl = SongList()
        assert sl.is_valid_song("/nonexistent/song.mp4") is False

    def test_add_if_valid_success(self, tmp_path):
        """Test add_if_valid with valid file."""
        sl = SongList()
        song_file = tmp_path / "song.mp4"
        song_file.touch()
        result = sl.add_if_valid(str(song_file))
        assert result is True
        assert str(song_file) in sl

    def test_add_if_valid_failure(self, tmp_path):
        """Test add_if_valid with invalid file."""
        sl = SongList()
        song_file = tmp_path / "song.txt"
        song_file.touch()
        result = sl.add_if_valid(str(song_file))
        assert result is False
        assert str(song_file) not in sl


class TestSongListIteration:
    """Tests for SongList iteration and indexing."""

    def test_iteration_sorted(self):
        """Test that iteration returns sorted songs."""
        sl = SongList()
        sl.add("/songs/zebra.mp4")
        sl.add("/songs/apple.mp4")
        sl.add("/songs/mango.mp4")
        songs = list(sl)
        assert songs == ["/songs/apple.mp4", "/songs/mango.mp4", "/songs/zebra.mp4"]

    def test_indexing(self):
        """Test accessing songs by index."""
        sl = SongList()
        sl.add("/songs/b.mp4")
        sl.add("/songs/a.mp4")
        assert sl[0] == "/songs/a.mp4"
        assert sl[1] == "/songs/b.mp4"

    def test_slicing(self):
        """Test slicing the song list."""
        sl = SongList()
        sl.add("/songs/c.mp4")
        sl.add("/songs/b.mp4")
        sl.add("/songs/a.mp4")
        assert sl[0:2] == ["/songs/a.mp4", "/songs/b.mp4"]

    def test_bool_empty(self):
        """Test bool conversion for empty list."""
        sl = SongList()
        assert bool(sl) is False

    def test_bool_non_empty(self):
        """Test bool conversion for non-empty list."""
        sl = SongList()
        sl.add("/songs/test.mp4")
        assert bool(sl) is True

    def test_copy(self):
        """Test creating a copy of the song list."""
        sl = SongList()
        sl.add("/songs/test.mp4")
        copy = sl.copy()
        assert copy == ["/songs/test.mp4"]
        assert isinstance(copy, list)


class TestSongListRename:
    """Tests for SongList rename operation."""

    def test_rename_valid(self, tmp_path):
        """Test renaming a song to a valid path."""
        sl = SongList()
        old_file = tmp_path / "old.mp4"
        new_file = tmp_path / "new.mp4"
        old_file.touch()
        new_file.touch()

        sl.add(str(old_file))
        result = sl.rename(str(old_file), str(new_file))

        assert result is True
        assert str(old_file) not in sl
        assert str(new_file) in sl

    def test_rename_to_invalid_fails(self, tmp_path):
        """Test renaming to invalid path fails."""
        sl = SongList()
        old_file = tmp_path / "old.mp4"
        old_file.touch()
        sl.add(str(old_file))

        result = sl.rename(str(old_file), "/nonexistent/new.mp4")

        assert result is False
        assert str(old_file) not in sl  # Old path removed


class TestSongListScanDirectory:
    """Tests for SongList scan_directory operation."""

    def test_scan_directory_finds_songs(self, tmp_path):
        """Test scanning directory finds valid song files."""
        sl = SongList()
        (tmp_path / "song1.mp4").touch()
        (tmp_path / "song2.webm").touch()
        (tmp_path / "song3.mp3").touch()
        (tmp_path / "readme.txt").touch()

        count = sl.scan_directory(str(tmp_path))

        assert count == 3
        assert len(sl) == 3

    def test_scan_directory_recursive(self, tmp_path):
        """Test scanning finds songs in subdirectories."""
        sl = SongList()
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "song1.mp4").touch()
        (subdir / "song2.mp4").touch()

        count = sl.scan_directory(str(tmp_path))

        assert count == 2

    def test_scan_directory_replaces_existing(self, tmp_path):
        """Test that scanning replaces existing songs."""
        sl = SongList()
        sl.add("/old/song.mp4")
        (tmp_path / "new.mp4").touch()

        sl.scan_directory(str(tmp_path))

        assert "/old/song.mp4" not in sl
        assert len(sl) == 1


class TestSongListCacheInvalidation:
    """Tests for SongList cache behavior."""

    def test_cache_invalidated_on_add(self):
        """Test that cache is invalidated when adding."""
        sl = SongList()
        sl.add("/songs/b.mp4")
        _ = list(sl)  # Populate cache
        sl.add("/songs/a.mp4")
        assert sl[0] == "/songs/a.mp4"  # Should reflect new sort

    def test_cache_invalidated_on_remove(self):
        """Test that cache is invalidated when removing."""
        sl = SongList()
        sl.add("/songs/a.mp4")
        sl.add("/songs/b.mp4")
        _ = list(sl)  # Populate cache
        sl.remove("/songs/a.mp4")
        assert len(list(sl)) == 1
        assert sl[0] == "/songs/b.mp4"


class TestSongListFindById:
    """Tests for find_by_id with special characters in filenames.

    These tests prevent regressions where special characters in song titles
    (common in non-English songs) break the download and queue functionality.
    See commit f399b57 for the original fix.
    """

    @pytest.mark.parametrize(
        "filename,video_id",
        [
            ("Babymetal - ギミチョコ---dQw4w9WgXcQ.mp4", "dQw4w9WgXcQ"),
            ("노래 제목---abc12345678.mp4", "abc12345678"),
            ("Tom & Jerry - What's Up---xyz98765432.mp4", "xyz98765432"),
            ("Song (Official Video) [4K]---qrs11223344.mp4", "qrs11223344"),
            ("L'Arc~en~Ciel - Ready Steady Go!---mno55667788.mp4", "mno55667788"),
        ],
        ids=["japanese", "korean", "quotes_ampersand", "parentheses_brackets", "mixed_unicode"],
    )
    def test_find_by_id_special_characters(self, tmp_path, filename, video_id):
        """Test finding files with special characters in filenames."""
        sl = SongList()
        (tmp_path / filename).touch()

        result = sl.find_by_id(str(tmp_path), video_id)

        assert result is not None
        assert video_id in result

    def test_find_by_id_not_found(self, tmp_path):
        """Test that find_by_id returns None when ID not found."""
        sl = SongList()
        (tmp_path / "Some Song---abc123.mp4").touch()

        result = sl.find_by_id(str(tmp_path), "nonexistent")

        assert result is None

    def test_find_by_id_validates_extension(self, tmp_path):
        """Test that find_by_id only returns valid song files."""
        sl = SongList()
        (tmp_path / "Song---abc123.txt").touch()

        result = sl.find_by_id(str(tmp_path), "abc123")

        assert result is None
