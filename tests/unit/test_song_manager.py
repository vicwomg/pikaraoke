"""Unit tests for SongManager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.song_manager import SongManager, rename_collides


def _native(path: Path) -> str:
    """Convert Path to native OS string to match SongList's internal storage format."""
    return str(path)


@pytest.fixture
def mock_db():
    db = MagicMock()
    # (song_id, youtube_id), which rename() reads to announce song_renamed.
    db.get_song_identity.return_value = (1, None)
    return db


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
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not song.exists()
        assert len(sm.songs) == 0

    def test_deletes_cdg_companion(self, tmp_path, mock_db):
        song = tmp_path / "Test---abc.mp4"
        cdg = tmp_path / "Test---abc.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not cdg.exists()

    def test_deletes_ass_companion(self, tmp_path, mock_db):
        song = tmp_path / "Test---abc.mp4"
        ass = tmp_path / "Test---abc.ass"
        song.write_text("fake")
        ass.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        assert not ass.exists()

    def test_nonexistent_file_no_error(self, tmp_path, mock_db):
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.delete(_native(tmp_path / "nonexistent.mp4"))


class TestRename:
    def test_renames_file_and_updates_songs(self, tmp_path, mock_db):
        song = tmp_path / "Old Name---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New Name---abc")
        assert not song.exists()
        assert (tmp_path / "New Name---abc.mp4").exists()

    def test_renames_a_song_to_the_same_name_in_another_case(self, tmp_path, mock_db):
        """NTFS and APFS do not see this as a move, so rename hops via a temp name."""
        song = tmp_path / "old name---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "Old Name---abc")
        assert [f.name for f in tmp_path.iterdir()] == ["Old Name---abc.mp4"]

    def test_a_song_already_at_the_target_is_never_overwritten(self, tmp_path, mock_db):
        """The route checks first, but that check is outside the lock this runs under."""
        song = tmp_path / "Old---abc.mp4"
        taken = tmp_path / "Taken---def.mp4"
        song.write_text("fake")
        taken.write_text("keep me")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))

        with pytest.raises(FileExistsError) as excinfo:
            sm.rename(_native(song), "Taken---def")

        # Our refusal, not the one os.rename raises for itself on Windows only.
        assert "'Taken---def.mp4' already exists" in str(excinfo.value)
        assert taken.read_text() == "keep me"
        assert song.exists()

    def test_renames_cdg_companion(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        cdg = tmp_path / "Old---abc.cdg"
        song.write_text("fake")
        cdg.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.cdg").exists()
        assert not cdg.exists()

    def test_renames_ass_companion(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        ass = tmp_path / "Old---abc.ass"
        song.write_text("fake")
        ass.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        assert (tmp_path / "New---abc.ass").exists()
        assert not ass.exists()

    def test_returns_new_path(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        result = sm.rename(_native(song), "New---abc")
        assert result == _native(tmp_path / "New---abc.mp4")

    def test_a_song_in_a_subfolder_stays_there(self, tmp_path, mock_db):
        """The scanner walks recursively; renaming used to move songs to the root."""
        subfolder = tmp_path / "Duets"
        subfolder.mkdir()
        song = subfolder / "Old---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))

        result = sm.rename(_native(song), "New---abc")

        assert result == _native(subfolder / "New---abc.mp4")
        assert (subfolder / "New---abc.mp4").exists()
        assert not (tmp_path / "New---abc.mp4").exists()

    def test_a_companion_follows_into_the_subfolder(self, tmp_path, mock_db):
        subfolder = tmp_path / "Duets"
        subfolder.mkdir()
        (subfolder / "Old---abc.mp3").write_text("fake")
        (subfolder / "Old---abc.cdg").write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(subfolder / "Old---abc.mp3"))

        sm.rename(_native(subfolder / "Old---abc.mp3"), "New---abc")

        assert (subfolder / "New---abc.cdg").exists()


class TestRenameCollides:
    """Only a different song's file is a clash, whatever the filesystem makes of case."""

    def test_a_free_name_does_not_collide(self, tmp_path):
        song = tmp_path / "Old.mp4"
        song.write_text("fake")
        assert not rename_collides(_native(song), _native(tmp_path / "New.mp4"))

    def test_another_song_collides(self, tmp_path):
        song = tmp_path / "Old.mp4"
        taken = tmp_path / "Taken.mp4"
        song.write_text("fake")
        taken.write_text("fake")
        assert rename_collides(_native(song), _native(taken))

    def test_changing_only_the_case_does_not_collide(self, tmp_path):
        """The target 'exists' on a case-insensitive filesystem because it is this song."""
        song = tmp_path / "Old.mp4"
        song.write_text("fake")
        assert not rename_collides(_native(song), _native(tmp_path / "OLD.mp4"))

    def test_a_vanished_song_cannot_claim_the_target(self, tmp_path):
        taken = tmp_path / "Taken.mp4"
        taken.write_text("fake")
        assert rename_collides(_native(tmp_path / "Gone.mp4"), _native(taken))


class TestRenameTarget:
    """The clash check reads this path, so it has to be the one rename writes to."""

    def test_it_is_where_rename_writes_a_name_that_needs_sanitizing(self, tmp_path, mock_db):
        song = tmp_path / "Old---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))

        target = sm.rename_target(_native(song), "AC/DC - Thunderstruck---abc")

        assert sm.rename(_native(song), "AC/DC - Thunderstruck---abc") == target
        assert Path(target).exists()

    def test_it_keeps_the_song_in_its_subfolder(self, tmp_path, mock_db):
        subfolder = tmp_path / "Duets"
        subfolder.mkdir()
        song = subfolder / "Old---abc.mp4"
        song.write_text("fake")
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())

        assert sm.rename_target(_native(song), "New---abc") == _native(subfolder / "New---abc.mp4")


class TestDBCoordination:
    """Tests that SongManager coordinates with KaraokeDatabase when provided."""

    def test_delete_calls_db_delete(self, tmp_path):
        song = tmp_path / "Test---abc.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.delete(_native(song))
        mock_db.delete_by_path.assert_called_once_with(_native(song))

    def test_rename_calls_db_update_path(self, tmp_path):
        song = tmp_path / "Old---abc.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.songs.add_if_valid(_native(song))
        sm.rename(_native(song), "New---abc")
        mock_db.update_path.assert_called_once_with(
            _native(song), _native(tmp_path / "New---abc.mp4")
        )

    def test_register_download_adds_to_songs_and_db(self, tmp_path):
        song = tmp_path / "New---xyz12345678.mp4"
        song.write_text("fake")
        mock_db = MagicMock()
        sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem())
        sm.register_download(_native(song))
        assert _native(song) in sm.songs
        mock_db.insert_songs.assert_called_once()


# enable_title_tidy defaults to False, so every test that cares about it sets it
# explicitly -- otherwise a test silently exercises only the untidied path.
def _manager(tmp_path, mock_db, songs, tidy=False):
    sm = SongManager(str(tmp_path), db=mock_db, events=EventSystem(), get_title_tidy=lambda: tidy)
    sm.songs.update(songs)
    return sm


class TestSearch:
    """Tests for filename matching."""

    def test_single_term(self, tmp_path, mock_db):
        sm = _manager(
            tmp_path,
            mock_db,
            [
                "/songs/Abba - Waterloo---aaaaaaaaaaa.mp4",
                "/songs/Queen - Bohemian---bbbbbbbbbbb.mp4",
            ],
        )
        assert sm.search("waterloo") == ["/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"]

    def test_multi_term_is_order_independent(self, tmp_path, mock_db):
        song = "/songs/Queen - Bohemian Rhapsody---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.search("queen bohemian") == [song]
        assert sm.search("bohemian queen") == [song]

    def test_case_insensitive(self, tmp_path, mock_db):
        song = "/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.search("WATERLOO") == [song]

    def test_empty_query_returns_everything(self, tmp_path, mock_db):
        songs = ["/songs/a---aaaaaaaaaaa.mp4", "/songs/b---bbbbbbbbbbb.mp4"]
        sm = _manager(tmp_path, mock_db, songs)
        assert sm.search("") == sorted(songs)
        assert sm.search("   ") == sorted(songs)

    def test_single_and_multi_term_paths_agree(self, tmp_path, mock_db):
        """The single-term fast path must return what the general path would."""
        songs = [
            "/songs/Abba - Waterloo---aaaaaaaaaaa.mp4",
            "/songs/Abba - Dancing Queen---bbbbbbbbbbb.mp4",
        ]
        sm = _manager(tmp_path, mock_db, songs)
        assert sm.search("abba") == sm.search("abba abba")

    def test_accent_insensitive_both_directions(self, tmp_path, mock_db):
        song = "/songs/Céline Dion - Pour que tu m'aimes---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.search("celine") == [song]
        assert sm.search("céline") == [song]

    def test_no_match_returns_empty(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, ["/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"])
        assert sm.search("nonexistent") == []

    def test_ignores_directory_names(self, tmp_path, mock_db):
        """Matching the full path would return every song in a library under Karaoke/."""
        song = "/music/Karaoke/Abba - Waterloo---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.search("karaoke") == []
        assert sm.search("waterloo") == [song]


class TestSearchUsesUntidiedFilename:
    """The match key is the raw stem, so terms regex_tidy strips are still findable."""

    SONG = "/songs/Bohemian Rhapsody (Karaoke Version)---aaaaaaaaaaa.mp4"

    def test_finds_song_by_a_word_tidy_strips(self, tmp_path, mock_db):
        """The deciding case: a tidied key cannot contain 'karaoke' by construction."""
        sm = _manager(tmp_path, mock_db, [self.SONG], tidy=True)
        assert sm.search("bohemian karaoke") == [self.SONG]

    def test_results_identical_regardless_of_tidy_preference(self, tmp_path, mock_db):
        songs = [self.SONG, "/songs/Abba - Waterloo HD---bbbbbbbbbbb.mp4"]
        tidy_on = _manager(tmp_path, mock_db, songs, tidy=True)
        tidy_off = _manager(tmp_path, mock_db, songs, tidy=False)
        for query in ("bohemian karaoke", "waterloo hd", "abba", "version"):
            assert tidy_on.search(query) == tidy_off.search(query)


class TestMatchIndexInvalidation:
    """The index is keyed on SongList.version, which is the part most likely to break."""

    def test_song_added_after_a_search_is_findable(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, ["/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"])
        assert sm.search("dancing") == []
        new_song = "/songs/Abba - Dancing Queen---bbbbbbbbbbb.mp4"
        sm.songs.add(new_song)
        assert sm.search("dancing") == [new_song]

    def test_deleted_song_disappears(self, tmp_path, mock_db):
        song = "/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.search("waterloo") == [song]
        sm.songs.remove(song)
        assert sm.search("waterloo") == []

    def test_renamed_song_findable_under_new_name_only(self, tmp_path, mock_db):
        song = tmp_path / "Old Title---aaaaaaaaaaa.mp4"
        song.write_text("fake")
        sm = SongManager(
            str(tmp_path), db=mock_db, events=EventSystem(), get_title_tidy=lambda: False
        )
        sm.songs.add_if_valid(str(song))
        assert sm.search("old") == [str(song)]
        new_path = sm.rename(str(song), "New Title---aaaaaaaaaaa")
        assert sm.search("new") == [new_path]
        assert sm.search("old") == []


class TestSongsByLetter:
    def test_groups_by_first_letter(self, tmp_path, mock_db):
        abba = "/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"
        queen = "/songs/Queen - Bohemian---bbbbbbbbbbb.mp4"
        sm = _manager(tmp_path, mock_db, [abba, queen])
        assert sm.songs_by_letter("a") == [abba]
        assert sm.songs_by_letter("q") == [queen]

    def test_accent_folding(self, tmp_path, mock_db):
        song = "/songs/Édith Piaf - La Vie en Rose---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.songs_by_letter("e") == [song]

    def test_numeric(self, tmp_path, mock_db):
        numeric = "/songs/99 Luftballons---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [numeric, "/songs/Abba - Waterloo---bbbbbbbbbbb.mp4"])
        assert sm.songs_by_letter("numeric") == [numeric]

    def test_case_insensitive_input(self, tmp_path, mock_db):
        song = "/songs/Abba - Waterloo---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song])
        assert sm.songs_by_letter("A") == [song]

    def test_groups_by_raw_filename_not_display_name(self, tmp_path, mock_db):
        """Filing under the raw name is what keeps the alpha bar in step with the sort order."""
        song = "/songs/Karaoke - Bohemian Rhapsody---aaaaaaaaaaa.mp4"
        sm = _manager(tmp_path, mock_db, [song], tidy=True)
        assert sm.display_name_from_path(song) == "Bohemian Rhapsody"
        assert sm.songs_by_letter("k") == [song]
        assert sm.songs_by_letter("b") == []


class TestDisplayNameUnchanged:
    """Regression guard: this change touches matching and must leave display alone."""

    SONG = "/songs/Queen - Bohemian Rhapsody (Karaoke Version)---aaaaaaaaaaa.mp4"

    def test_tidy_on_returns_tidied_name(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, [self.SONG], tidy=True)
        assert sm.display_name_from_path(self.SONG) == "Queen - Bohemian Rhapsody"

    def test_tidy_off_returns_raw_name(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, [self.SONG], tidy=False)
        assert sm.display_name_from_path(self.SONG) == "Queen - Bohemian Rhapsody (Karaoke Version)"


def _under(tmp_path, *relative: str) -> list[str]:
    """Native paths for songs genuinely inside the manager's download path."""
    return [str(tmp_path.joinpath(*r.split("/"))) for r in relative]


class TestFolderTree:
    """The tree is what makes a folder key trustworthy: it is built from real songs."""

    def test_root_lists_immediate_subfolders_only(self, tmp_path, mock_db):
        songs = _under(tmp_path, "Top.mp4", "Rock/T.mp4", "Pop/T.mp4", "Rock/Metal/H.mp4")
        sm = _manager(tmp_path, mock_db, songs)
        assert sm.folder_tree()[""] == ["Pop", "Rock"]

    def test_every_level_is_its_own_key(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "Rock/Metal/Heavy.mp4"))
        tree = sm.folder_tree()
        assert tree["Rock"] == ["Metal"]
        # A leaf still has to be a key, or browsing into it clamps back to the root.
        assert tree["Rock/Metal"] == []

    def test_flat_library_has_only_the_root(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "A.mp4", "B.mp4"))
        assert sm.folder_tree() == {"": []}

    def test_subfolders_sort_case_insensitively(self, tmp_path, mock_db):
        songs = _under(tmp_path, "zed/a.mp4", "Alpha/a.mp4", "beta/a.mp4")
        sm = _manager(tmp_path, mock_db, songs)
        assert sm.folder_tree()[""] == ["Alpha", "beta", "zed"]

    def test_rebuilds_when_the_song_list_changes(self, tmp_path, mock_db):
        """Memoized on SongList.version, so a stale tree would outlive a download."""
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "Rock/a.mp4"))
        assert sm.folder_tree()[""] == ["Rock"]
        sm.songs.update(_under(tmp_path, "Rock/a.mp4", "Jazz/b.mp4"))
        assert sm.folder_tree()[""] == ["Jazz", "Rock"]


class TestFolderScoping:
    SONGS = ("Loose.mp4", "Rock/Bon Jovi.mp4", "Rock/Metal/Heavy.mp4", "Pop/Abba.mp4")

    def test_songs_in_folder_excludes_deeper_levels(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert sm.songs_in_folder("Rock") == _under(tmp_path, "Rock/Bon Jovi.mp4")

    def test_root_folder_excludes_everything_in_a_subfolder(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert sm.songs_in_folder("") == _under(tmp_path, "Loose.mp4")

    def test_no_scope_is_not_the_same_as_the_root(self, tmp_path, mock_db):
        """The flat view lists every song wherever it sits; the folder root does not."""
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert len(sm.search("", folder=None)) == 4
        assert len(sm.search("", folder="")) == 1

    def test_search_is_scoped_to_the_folder(self, tmp_path, mock_db):
        songs = _under(tmp_path, "Rock/Abba - Waterloo.mp4", "Pop/Abba - Dancing.mp4")
        sm = _manager(tmp_path, mock_db, songs)
        assert sm.search("abba", folder="Rock") == _under(tmp_path, "Rock/Abba - Waterloo.mp4")
        assert len(sm.search("abba")) == 2

    def test_songs_by_letter_is_scoped_to_the_folder(self, tmp_path, mock_db):
        songs = _under(tmp_path, "Rock/Abba.mp4", "Pop/Adele.mp4")
        sm = _manager(tmp_path, mock_db, songs)
        assert sm.songs_by_letter("a", folder="Rock") == _under(tmp_path, "Rock/Abba.mp4")
        assert len(sm.songs_by_letter("a")) == 2

    def test_unknown_folder_yields_nothing(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert sm.songs_in_folder("Nope") == []


class TestLettersWithSongs:
    """The alpha bar greys the keys that would return nothing, so the two must agree."""

    SONGS = ("Loose.mp4", "Rock/Bon Jovi.mp4", "Rock/Metal/Heavy.mp4", "Pop/Abba.mp4")

    def test_a_folder_reports_only_its_direct_children(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert sm.letters_with_songs("Rock") == {"b"}

    def test_no_scope_covers_the_whole_library(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert sm.letters_with_songs() == {"l", "b", "h", "a"}

    def test_the_root_is_not_the_whole_library(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        assert sm.letters_with_songs("") == {"l"}

    def test_a_folder_of_subfolders_reports_nothing(self, tmp_path, mock_db):
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "Genres/Rock/Heavy.mp4"))
        assert sm.letters_with_songs("Genres") == set()

    def test_a_digit_reports_as_numeric(self, tmp_path, mock_db):
        """Matching songs_by_letter, which groups every digit under one key."""
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "99 Luftballons.mp4"))
        assert sm.letters_with_songs() == {"numeric"}

    def test_accents_fold_onto_the_plain_letter(self, tmp_path, mock_db):
        """songs_by_letter matches the folded name, so 'b' must light up for Bjork."""
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "Bjork - Army Of Me.mp4"))
        assert sm.letters_with_songs() == {"b"}

    def test_every_reported_letter_actually_returns_songs(self, tmp_path, mock_db):
        """The invariant behind the greying: a linked key is never a dead end."""
        sm = _manager(tmp_path, mock_db, _under(tmp_path, *self.SONGS))
        for folder in ("", "Rock", "Rock/Metal", "Pop", None):
            for key in sm.letters_with_songs(folder):
                assert sm.songs_by_letter(key, folder=folder), f"{key!r} dead in {folder!r}"

    def test_rebuilds_when_the_song_list_changes(self, tmp_path, mock_db):
        """Memoized on SongList.version, so a download would otherwise stay greyed out."""
        sm = _manager(tmp_path, mock_db, _under(tmp_path, "Rock/Bon Jovi.mp4"))
        assert sm.letters_with_songs("Rock") == {"b"}
        sm.songs.update(_under(tmp_path, "Rock/Bon Jovi.mp4", "Rock/Queen.mp4"))
        assert sm.letters_with_songs("Rock") == {"b", "q"}
