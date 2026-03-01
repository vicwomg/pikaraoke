"""Unit tests for batch_song_renamer route-level logic."""

from pikaraoke.routes.batch_song_renamer import _names_match


class TestNamesMatch:
    """Tests for the _names_match comparison function."""

    def test_identical_names_match(self):
        assert _names_match("Artist - Song", "Artist - Song") is True

    def test_case_insensitive(self):
        assert _names_match("artist - song", "Artist - Song") is True

    def test_dash_variants_match(self):
        assert _names_match("Artist \u2013 Song", "Artist - Song") is True

    def test_whitespace_normalized(self):
        assert _names_match("Artist  -  Song", "Artist - Song") is True

    def test_accent_insensitive(self):
        assert _names_match("C\u00e9line Dion - Song", "Celine Dion - Song") is True

    def test_none_correct_name(self):
        assert _names_match("Artist - Song", None) is False

    def test_different_names(self):
        assert _names_match("Artist - Song A", "Artist - Song B") is False

    def test_empty_strings(self):
        assert _names_match("", "") is True
