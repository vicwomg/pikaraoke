"""Unit tests for SongManager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.song_manager import SongManager


def _native(path: Path) -> str:
    """Convert Path to native OS string to match SongList's internal storage format."""
    return str(path)


@pytest.fixture
def mock_db():
    """Minimal mock that keeps the SongManager.delete/rename code paths quiet.

    Returns an empty artifact list so the artifact loop short-circuits; the
    tests that exercise real artifact-driven cleanup use the ``real_db``
    fixture instead.
    """
    db = MagicMock()
    db.get_song_id_by_path.return_value = None
    db.get_artifacts.return_value = []
    return db


@pytest.fixture
def real_db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


def _register(sm: SongManager, db: KaraokeDatabase, song_path: str) -> int:
    """Insert a song + discover its artifacts on disk. Returns the song_id.

    Assumes the SongManager was created with ``enrich_on_download=False``
    (the helper above uses ``real_db``/``mock_db`` fixtures which both wire
    SongManager that way); otherwise this would fire network traffic.
    """
    sm.register_download(song_path)
    return db.get_song_id_by_path(song_path)


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
    def test_removes_file_and_updates_songs(self, tmp_path, real_db):
        song = tmp_path / "Test---abc12345678.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.delete(_native(song))
        assert not song.exists()
        assert len(sm.songs) == 0

    def test_deletes_cdg_companion(self, tmp_path, real_db):
        song = tmp_path / "Test---abc12345678.mp4"
        cdg = tmp_path / "Test---abc12345678.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.delete(_native(song))
        assert not cdg.exists()

    def test_deletes_ass_auto_companion(self, tmp_path, real_db):
        song = tmp_path / "Test---abc12345678.mp4"
        ass = tmp_path / "Test---abc12345678.ass"
        song.write_text("fake")
        ass.write_text("Title: PiKaraoke Auto-Lyrics\n")  # has marker -> ass_auto
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.delete(_native(song))
        assert not ass.exists()

    def test_preserves_user_ass(self, tmp_path, real_db):
        """User-authored .ass (no PiKaraoke marker) must survive delete."""
        song = tmp_path / "Test---abc12345678.mp4"
        ass = tmp_path / "Test---abc12345678.ass"
        song.write_text("fake")
        ass.write_text("[Script Info]\nTitle: my hand edit\n")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.delete(_native(song))
        assert ass.exists(), "user .ass should be preserved"

    def test_deletes_m4a_sibling(self, tmp_path, real_db):
        """Parallel-download songs leave an .m4a next to the silent .mp4."""
        song = tmp_path / "Test---abc12345678.mp4"
        m4a = tmp_path / "Test---abc12345678.m4a"
        song.write_text("fake")
        m4a.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.delete(_native(song))
        assert not m4a.exists()

    def test_deletes_info_json_and_vtt(self, tmp_path, real_db):
        """yt-dlp byproducts that the old delete ignored now get cleaned up."""
        song = tmp_path / "Test---abc12345678.mp4"
        info = tmp_path / "Test---abc12345678.info.json"
        vtt = tmp_path / "Test---abc12345678.en.vtt"
        song.write_text("fake")
        info.write_text("{}")
        vtt.write_text("WEBVTT\n")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.delete(_native(song))
        assert not info.exists()
        assert not vtt.exists()

    def test_rmtrees_stems_cache_when_sole_owner(self, tmp_path, real_db):
        song = tmp_path / "Test---abc12345678.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        sid = _register(sm, real_db, _native(song))

        cache_dir = tmp_path / "cache" / ("a" * 64)
        cache_dir.mkdir(parents=True)
        (cache_dir / "vocals.wav").write_bytes(b"x")
        real_db.update_audio_fingerprint(sid, 0.0, 0, "a" * 64)
        real_db.upsert_artifacts(sid, [{"role": "stems_cache_dir", "path": str(cache_dir)}])

        sm.delete(_native(song))
        assert not cache_dir.exists()

    def test_keeps_stems_cache_when_shared(self, tmp_path, real_db):
        song_a = tmp_path / "A---abc12345678.mp4"
        song_b = tmp_path / "B---def12345678.mp4"
        song_a.write_text("fake")
        song_b.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        sid_a = _register(sm, real_db, _native(song_a))
        sid_b = _register(sm, real_db, _native(song_b))

        sha = "a" * 64
        cache_dir = tmp_path / "cache" / sha
        cache_dir.mkdir(parents=True)
        real_db.update_audio_fingerprint(sid_a, 0.0, 0, sha)
        real_db.update_audio_fingerprint(sid_b, 0.0, 0, sha)
        real_db.upsert_artifacts(sid_a, [{"role": "stems_cache_dir", "path": str(cache_dir)}])
        real_db.upsert_artifacts(sid_b, [{"role": "stems_cache_dir", "path": str(cache_dir)}])

        sm.delete(_native(song_a))
        assert cache_dir.exists(), "B still references this cache dir"

    def test_nonexistent_file_no_error(self, tmp_path, mock_db):
        sm = SongManager(str(tmp_path), db=mock_db, enrich_on_download=False)
        sm.delete(_native(tmp_path / "nonexistent.mp4"))


class TestRename:
    def test_renames_file_and_updates_songs(self, tmp_path, real_db):
        song = tmp_path / "Old Name---abc12345678.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.rename(_native(song), "New Name---abc12345678")
        assert not song.exists()
        assert (tmp_path / "New Name---abc12345678.mp4").exists()

    def test_renames_cdg_companion(self, tmp_path, real_db):
        song = tmp_path / "Old---abc12345678.mp4"
        cdg = tmp_path / "Old---abc12345678.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.rename(_native(song), "New---abc12345678")
        assert (tmp_path / "New---abc12345678.cdg").exists()
        assert not cdg.exists()

    def test_renames_ass_companion(self, tmp_path, real_db):
        song = tmp_path / "Old---abc12345678.mp4"
        ass = tmp_path / "Old---abc12345678.ass"
        song.write_text("fake")
        ass.write_text("Title: PiKaraoke Auto-Lyrics\n")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.rename(_native(song), "New---abc12345678")
        assert (tmp_path / "New---abc12345678.ass").exists()
        assert not ass.exists()

    def test_renames_m4a_sibling(self, tmp_path, real_db):
        song = tmp_path / "Old---abc12345678.mp4"
        m4a = tmp_path / "Old---abc12345678.m4a"
        song.write_text("fake")
        m4a.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.rename(_native(song), "New---abc12345678")
        assert (tmp_path / "New---abc12345678.m4a").exists()
        assert not m4a.exists()

    def test_renames_vtt_with_language_suffix(self, tmp_path, real_db):
        song = tmp_path / "Old---abc12345678.mp4"
        vtt = tmp_path / "Old---abc12345678.en.vtt"
        song.write_text("fake")
        vtt.write_text("WEBVTT\n")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        sm.rename(_native(song), "New---abc12345678")
        assert (tmp_path / "New---abc12345678.en.vtt").exists()
        assert not vtt.exists()

    def test_preserves_stems_cache_dir_path(self, tmp_path, real_db):
        """Rename must leave the content-addressed stems cache path unchanged."""
        song = tmp_path / "Old---abc12345678.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        sid = _register(sm, real_db, _native(song))
        cache_path = str(tmp_path / "cache" / ("a" * 64))
        real_db.upsert_artifacts(sid, [{"role": "stems_cache_dir", "path": cache_path}])

        new_path = sm.rename(_native(song), "New---abc12345678")

        new_sid = real_db.get_song_id_by_path(new_path)
        arts = {(a["role"], a["path"]) for a in real_db.get_artifacts(new_sid)}
        assert ("stems_cache_dir", cache_path) in arts

    def test_returns_new_path(self, tmp_path, real_db):
        song = tmp_path / "Old---abc12345678.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=real_db, enrich_on_download=False)
        _register(sm, real_db, _native(song))
        result = sm.rename(_native(song), "New---abc12345678")
        assert result == _native(tmp_path / "New---abc12345678.mp4")


class TestDBCoordination:
    """Tests that SongManager coordinates with KaraokeDatabase when provided."""

    def test_delete_calls_db_delete(self, tmp_path):
        song = tmp_path / "Test---abc.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db, enrich_on_download=False)
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        mock_db.delete_by_path.assert_called_once_with(_native(song))

    def test_rename_calls_db_update_path(self, tmp_path):
        song = tmp_path / "Old---abc.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db, enrich_on_download=False)
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        mock_db.update_path.assert_called_once_with(
            _native(song), _native(tmp_path / "New---abc.mp4")
        )

    def test_register_download_adds_to_songs_and_db(self, tmp_path):
        song = tmp_path / "New---xyz12345678.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db, enrich_on_download=False)
        sm.register_download(_native(song))
        assert _native(song) in sm.songs
        mock_db.insert_songs.assert_called_once()
        (records,) = mock_db.insert_songs.call_args.args
        assert records == [
            {
                "file_path": _native(song),
                "youtube_id": "xyz12345678",
                "format": "mp4",
            }
        ]
