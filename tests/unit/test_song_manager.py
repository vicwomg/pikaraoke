"""Unit tests for SongManager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.song_manager import SongManager


def _native(path: Path) -> str:
    """Convert Path to native OS string to match SongList's internal storage format."""
    return str(path)


@pytest.fixture
def mock_db():
    return MagicMock()


class TestFilenameFromPath:
    """Tests for the filename_from_path static method."""

    def test_basic(self):
        assert SongManager.filename_from_path("/songs/My Song.mp4") == "My Song"

    def test_with_youtube_id(self):
        result = SongManager.filename_from_path("/songs/Artist - Song Title---dQw4w9WgXcQ.mp4")
        assert result == "Artist - Song Title"

    def test_keep_youtube_id(self):
        result = SongManager.filename_from_path(
            "/songs/Artist - Song---dQw4w9WgXcQ.mp4", remove_youtube_id=False
        )
        assert result == "Artist - Song---dQw4w9WgXcQ"

    def test_nested_directory(self):
        result = SongManager.filename_from_path(
            "/home/user/music/karaoke/songs/Track---dQw4w9WgXcQ.mp4"
        )
        assert result == "Track"

    def test_multiple_dashes(self):
        result = SongManager.filename_from_path(
            "/songs/Artist - Song - Live Version---dQw4w9WgXcQ.mp4"
        )
        assert result == "Artist - Song - Live Version"

    def test_no_extension(self):
        assert SongManager.filename_from_path("/songs/SongName") == "SongName"

    def test_cdg_zip(self):
        """CDG+ZIP files have no YouTube ID, so the name is returned as-is."""
        assert SongManager.filename_from_path("/songs/Karaoke Track.zip") == "Karaoke Track"

    def test_bracket_format_youtube_id(self):
        result = SongManager.filename_from_path("/songs/Artist - Song [dQw4w9WgXcQ].mp4")
        assert result == "Artist - Song"

    def test_bracket_format_keep_id(self):
        result = SongManager.filename_from_path(
            "/songs/Artist - Song [dQw4w9WgXcQ].mp4", remove_youtube_id=False
        )
        assert result == "Artist - Song [dQw4w9WgXcQ]"

    def test_bracket_format_short_id_not_stripped(self):
        """Non-YouTube files are not tidied, so short bracket text is preserved."""
        result = SongManager.filename_from_path("/songs/Song [short].mp4")
        assert result == "Song [short]"

    # --- regex_tidy integration: noise word stripping ---

    def test_strips_karaoke_noise_words(self):
        result = SongManager.filename_from_path(
            "/songs/Queen - Bohemian Rhapsody Karaoke Version HD---dQw4w9WgXcQ.mp4"
        )
        assert result == "Queen - Bohemian Rhapsody"

    def test_strips_instrumental_suffix(self):
        result = SongManager.filename_from_path(
            "/songs/Artist - Song Instrumental---dQw4w9WgXcQ.mp4"
        )
        assert result == "Artist - Song"

    def test_strips_lyrics_suffix(self):
        result = SongManager.filename_from_path(
            "/songs/Artist - Song With Lyrics---dQw4w9WgXcQ.mp4"
        )
        assert result == "Artist - Song"

    def test_underscores_replaced_with_spaces(self):
        result = SongManager.filename_from_path("/songs/Artist_-_Song_Title---dQw4w9WgXcQ.mp4")
        assert result == "Artist - Song Title"

    def test_all_noise_fallback_preserves_original(self):
        """When regex_tidy strips everything, fall back to the pre-tidy name."""
        result = SongManager.filename_from_path("/songs/Karaoke Track---dQw4w9WgXcQ.mp4")
        assert result == "Karaoke Track"

    def test_tidy_not_applied_when_keeping_youtube_id(self):
        """remove_youtube_id=False skips tidying (used by batch renamer for raw stems)."""
        result = SongManager.filename_from_path(
            "/songs/Artist - Song_Title Karaoke---dQw4w9WgXcQ.mp4",
            remove_youtube_id=False,
        )
        assert result == "Artist - Song_Title Karaoke---dQw4w9WgXcQ"


class TestDelete:
    def test_removes_file_and_updates_songs(self, tmp_path, mock_db):
        song = tmp_path / "Test---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not song.exists()
        assert len(sm.songs) == 0

    def test_deletes_cdg_companion(self, tmp_path, mock_db):
        song = tmp_path / "Test---abc.mp4"
        cdg = tmp_path / "Test---abc.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not cdg.exists()

    def test_deletes_ass_companion(self, tmp_path, mock_db):
        song = tmp_path / "Test---abc.mp4"
        ass = tmp_path / "Test---abc.ass"
        song.write_text("fake")
        ass.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not ass.exists()

    def test_deletes_m4a_sibling(self, tmp_path, mock_db):
        """Parallel-download songs leave an .m4a next to the silent .mp4."""
        song = tmp_path / "Test---abc.mp4"
        m4a = tmp_path / "Test---abc.m4a"
        song.write_text("fake")
        m4a.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not m4a.exists()

    def test_nonexistent_file_no_error(self, tmp_path, mock_db):
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.delete(_native(tmp_path / "nonexistent.mp4"))


class TestRename:
    def test_renames_file_and_updates_songs(self, tmp_path, mock_db):
        song = tmp_path / "Old Name---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New Name---abc")
        assert not song.exists()
        assert (tmp_path / "New Name---abc.mp4").exists()

    def test_renames_cdg_companion(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        cdg = tmp_path / "Old---abc.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.cdg").exists()
        assert not cdg.exists()

    def test_renames_ass_companion(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        ass = tmp_path / "Old---abc.ass"
        song.write_text("fake")
        ass.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.ass").exists()
        assert not ass.exists()

    def test_renames_m4a_sibling(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        m4a = tmp_path / "Old---abc.m4a"
        song.write_text("fake")
        m4a.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.m4a").exists()
        assert not m4a.exists()

    def test_returns_new_path(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        result = sm.rename(_native(song), "New---abc")
        assert result == _native(tmp_path / "New---abc.mp4")


class TestDBCoordination:
    """Tests that SongManager coordinates with KaraokeDatabase when provided."""

    def test_delete_calls_db_delete(self, tmp_path):
        song = tmp_path / "Test---abc.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        mock_db.delete_by_path.assert_called_once_with(_native(song))

    def test_rename_calls_db_update_path(self, tmp_path):
        song = tmp_path / "Old---abc.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        mock_db.update_path.assert_called_once_with(
            _native(song), _native(tmp_path / "New---abc.mp4")
        )

    def test_register_download_adds_to_songs_and_db(self, tmp_path):
        song = tmp_path / "New---xyz12345678.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db)
        sm.register_download(_native(song))
        assert _native(song) in sm.songs
        mock_db.insert_songs.assert_called_once()
