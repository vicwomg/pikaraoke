"""Unit tests for SongManager."""

from __future__ import annotations

from pathlib import Path

from pikaraoke.lib.song_manager import SongManager


def _native(path: Path) -> str:
    """Convert Path to native OS string to match SongList's internal storage format."""
    return str(path)


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
        result = SongManager.filename_from_path("/home/user/music/karaoke/songs/Track---abc123.mp4")
        assert result == "Track"

    def test_multiple_dashes(self):
        result = SongManager.filename_from_path("/songs/Artist - Song - Live Version---xyz789.mp4")
        assert result == "Artist - Song - Live Version"

    def test_no_extension(self):
        assert SongManager.filename_from_path("/songs/SongName") == "SongName"

    def test_cdg_zip(self):
        assert SongManager.filename_from_path("/songs/Karaoke Track---abc.zip") == "Karaoke Track"


class TestRefreshSongs:
    def test_scans_directory(self, tmp_path):
        (tmp_path / "song.mp4").write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        assert len(sm.songs) == 1

    def test_ignores_non_song_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("not a song")
        (tmp_path / "song.mp4").write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        assert len(sm.songs) == 1


class TestDelete:
    def test_removes_file_and_updates_songs(self, tmp_path):
        song = tmp_path / "Test---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        sm.delete(_native(song))
        assert not song.exists()
        assert len(sm.songs) == 0

    def test_deletes_cdg_companion(self, tmp_path):
        song = tmp_path / "Test---abc.mp4"
        cdg = tmp_path / "Test---abc.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        sm.delete(_native(song))
        assert not cdg.exists()

    def test_deletes_ass_companion(self, tmp_path):
        song = tmp_path / "Test---abc.mp4"
        ass = tmp_path / "Test---abc.ass"
        song.write_text("fake")
        ass.write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        sm.delete(_native(song))
        assert not ass.exists()

    def test_nonexistent_file_no_error(self, tmp_path):
        sm = SongManager(str(tmp_path))
        sm.delete(_native(tmp_path / "nonexistent.mp4"))


class TestRename:
    def test_renames_file_and_updates_songs(self, tmp_path):
        song = tmp_path / "Old Name---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        sm.rename(_native(song), "New Name---abc")
        assert not song.exists()
        assert (tmp_path / "New Name---abc.mp4").exists()

    def test_renames_cdg_companion(self, tmp_path):
        song = tmp_path / "Old---abc.mp4"
        cdg = tmp_path / "Old---abc.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.cdg").exists()
        assert not cdg.exists()

    def test_renames_ass_companion(self, tmp_path):
        song = tmp_path / "Old---abc.mp4"
        ass = tmp_path / "Old---abc.ass"
        song.write_text("fake")
        ass.write_text("fake")
        sm = SongManager(str(tmp_path))
        sm.refresh_songs()
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.ass").exists()
        assert not ass.exists()
