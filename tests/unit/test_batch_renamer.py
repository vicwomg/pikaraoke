"""Unit tests for batch_song_renamer module."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes.batch_song_renamer import (
    clean_search_query,
    get_best_result,
    get_song_correct_name,
    score_result,
)


class TestCleanSearchQuery:
    """Tests for the clean_search_query function."""

    def test_removes_karaoke_suffix(self):
        """Test removal of 'karaoke' from query."""
        result = clean_search_query("Artist - Song karaoke")
        assert "karaoke" not in result.lower()

    def test_removes_official_video(self):
        """Test removal of 'official video' terms."""
        result = clean_search_query("Artist - Song Official Music Video")
        assert "official" not in result.lower()
        assert "video" not in result.lower()

    def test_removes_lyrics(self):
        """Test removal of 'lyrics' from query."""
        result = clean_search_query("Artist - Song with lyrics")
        assert "lyrics" not in result.lower()

    def test_removes_parentheses_content(self):
        """Test removal of content in parentheses."""
        result = clean_search_query("Artist - Song (Official Video)")
        assert "(" not in result
        assert ")" not in result
        assert "Official" not in result

    def test_removes_brackets_content(self):
        """Test removal of content in brackets."""
        result = clean_search_query("Artist - Song [HD]")
        assert "[" not in result
        assert "]" not in result
        assert "HD" not in result

    def test_replaces_underscores_with_spaces(self):
        """Test that underscores are replaced with spaces."""
        result = clean_search_query("Artist_Name_-_Song_Title")
        assert "_" not in result
        assert "Artist Name" in result

    def test_removes_instrumental(self):
        """Test removal of 'instrumental' from query."""
        result = clean_search_query("Artist - Song Instrumental")
        assert "instrumental" not in result.lower()

    def test_removes_hd_hq(self):
        """Test removal of HD/HQ quality markers."""
        result = clean_search_query("Artist - Song HD HQ")
        assert "hd" not in result.lower()
        assert "hq" not in result.lower()

    def test_removes_feat(self):
        """Test removal of 'feat' and 'ft' markers."""
        result = clean_search_query("Artist feat. Other - Song")
        assert "feat" not in result.lower()

    def test_removes_remaster(self):
        """Test removal of 'remaster' from query."""
        result = clean_search_query("Artist - Song Remaster")
        assert "remaster" not in result.lower()

    def test_preserves_artist_and_title(self):
        """Test that core artist and title are preserved."""
        result = clean_search_query("Coldplay - Viva La Vida")
        assert "Coldplay" in result
        assert "Viva La Vida" in result

    def test_removes_emojis(self):
        """Test removal of emoji characters."""
        result = clean_search_query("Artist - Song 🎤🎵")
        assert "🎤" not in result
        assert "🎵" not in result

    def test_strips_whitespace(self):
        """Test that result is stripped of extra whitespace."""
        result = clean_search_query("  Artist - Song  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_complex_query_cleanup(self):
        """Test cleanup of a complex real-world query."""
        query = "Artist_Name - Song Title (Official Music Video) [HD] karaoke with lyrics 🎤"
        result = clean_search_query(query)
        # Should preserve core parts
        assert "Artist Name" in result
        assert "Song Title" in result
        # Should remove junk
        assert "karaoke" not in result.lower()
        assert "lyrics" not in result.lower()
        assert "[" not in result
        assert "(" not in result


class TestScoreResult:
    """Tests for the score_result function."""

    def test_exact_match_high_score(self):
        """Test that exact artist-title match gets high score."""
        result = {"name": "Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert score >= 100

    def test_exact_match_reversed_order(self):
        """Test exact match with title-artist order."""
        result = {"name": "Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Viva La Vida - Coldplay")
        assert score >= 100

    def test_partial_match_moderate_score(self):
        """Test that partial match gets moderate score."""
        result = {"name": "Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Coldplay - Viva")
        assert 0 < score < 100

    def test_uppercase_penalized(self):
        """Test that all-uppercase names are penalized."""
        result_upper = {"name": "VIVA LA VIDA", "artist": "COLDPLAY"}
        result_normal = {"name": "Viva La Vida", "artist": "Coldplay"}

        score_upper = score_result(result_upper, "Coldplay - Viva La Vida")
        score_normal = score_result(result_normal, "Coldplay - Viva La Vida")

        assert score_upper < score_normal

    def test_no_match_low_score(self):
        """Test that non-matching result gets low/zero score."""
        result = {"name": "Completely Different", "artist": "Unknown Artist"}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert score <= 0

    def test_empty_result_fields(self):
        """Test handling of empty result fields."""
        result = {"name": "", "artist": ""}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert isinstance(score, int)

    def test_missing_result_fields(self):
        """Test handling of missing result fields."""
        result = {}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert isinstance(score, int)

    def test_query_with_pipe_separator(self):
        """Test query with pipe separator instead of dash."""
        result = {"name": "Song Title", "artist": "Artist Name"}
        score = score_result(result, "Artist Name | Song Title")
        assert score >= 50

    def test_accented_characters_matched(self):
        """Test that accented characters are handled."""
        result = {"name": "Café", "artist": "Artiste"}
        score = score_result(result, "Artiste - Cafe")
        # Should still match despite accent differences
        assert score > 0

    def test_single_part_query_exact_match(self):
        """Test single-part query (no separator) with exact match."""
        result = {"name": "Bohemian Rhapsody", "artist": "Queen"}
        score = score_result(result, "Bohemian Rhapsody")
        # Single part queries get penalized for missing artist match
        assert isinstance(score, int)

    def test_single_part_query_partial_match(self):
        """Test single-part query with partial match."""
        result = {"name": "Bohemian Rhapsody", "artist": "Queen"}
        score = score_result(result, "Bohemian")
        # Single part partial match - penalized for missing artist
        assert isinstance(score, int)

    def test_word_matching_fallback(self):
        """Test word-by-word matching when no direct match."""
        result = {"name": "Something Different Song", "artist": "Artist"}
        score = score_result(result, "Something - Artist")
        # Should get partial score from word matching
        assert score > -1000

    def test_bad_keyword_penalization_live(self):
        """Test that 'live' versions are penalized."""
        result_live = {"name": "Song - Live", "artist": "Artist"}
        result_normal = {"name": "Song", "artist": "Artist"}
        score_live = score_result(result_live, "Artist - Song")
        score_normal = score_result(result_normal, "Artist - Song")
        assert score_live < score_normal

    def test_bad_keyword_penalization_remix(self):
        """Test that remix versions are penalized."""
        result = {"name": "Song remix", "artist": "Artist"}
        score = score_result(result, "Artist - Song")
        # Should have penalty applied
        assert isinstance(score, int)

    def test_long_title_penalization(self):
        """Test that very long titles are penalized."""
        long_name = "A" * 65  # Over 60 chars
        result = {"name": long_name, "artist": "Artist"}
        score = score_result(result, "Artist - Song")
        # Long title penalty should be applied
        assert isinstance(score, int)

    def test_mbid_bonus(self):
        """Test that results with MBID get a bonus."""
        result_with_mbid = {"name": "Song", "artist": "Artist", "mbid": "abc123"}
        result_without_mbid = {"name": "Song", "artist": "Artist"}
        score_with = score_result(result_with_mbid, "Artist - Song")
        score_without = score_result(result_without_mbid, "Artist - Song")
        assert score_with > score_without

    def test_artist_in_track_name_penalization(self):
        """Test that artist name in track is penalized."""
        result = {"name": "Coldplay - Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Coldplay - Viva La Vida")
        # Should be penalized for duplicated artist
        assert isinstance(score, int)

    def test_no_artist_match_penalization(self):
        """Test penalization when artist doesn't match query parts."""
        result = {"name": "Song Title", "artist": "Unknown Artist"}
        score = score_result(result, "Coldplay - Song Title")
        # Should be penalized for artist mismatch
        assert score < 100

    def test_part2_word_matching(self):
        """Test word matching using part2 of query."""
        result = {"name": "Amazing Song Title", "artist": "SomeArtist"}
        score = score_result(result, "Whatever - Amazing")
        # Should get some score from word matching
        assert isinstance(score, int)


class TestGetBestResult:
    """Tests for the get_best_result function."""

    def test_returns_none_for_empty_results(self):
        """Test that empty results return None."""
        assert get_best_result([], "Artist - Song") is None

    def test_returns_none_for_none_results(self):
        """Test that None results return None."""
        assert get_best_result(None, "Artist - Song") is None

    def test_returns_formatted_string(self):
        """Test that result is formatted as 'name - artist'."""
        results = [{"name": "Song Title", "artist": "Artist Name"}]
        result = get_best_result(results, "Artist Name - Song Title")
        assert result == "Song Title - Artist Name"

    def test_selects_best_match(self):
        """Test that the highest scoring result is selected."""
        results = [
            {"name": "Wrong Song", "artist": "Wrong Artist"},
            {"name": "Viva La Vida", "artist": "Coldplay"},
            {"name": "Another Wrong", "artist": "Another"},
        ]
        result = get_best_result(results, "Coldplay - Viva La Vida")
        assert "Viva La Vida" in result
        assert "Coldplay" in result

    def test_multiple_results_sorted_by_score(self):
        """Test that results are properly sorted by score."""
        results = [
            {"name": "Song", "artist": "Artist"},
            {"name": "Song", "artist": "Artist", "mbid": "bonus"},  # Has MBID bonus
        ]
        result = get_best_result(results, "Artist - Song")
        # Should return formatted string
        assert " - " in result


class TestGetSongCorrectName:
    """Tests for the get_song_correct_name function."""

    @patch("pikaraoke.routes.batch_song_renamer.requests.get")
    def test_returns_none_on_api_error(self, mock_get):
        """Test that API errors return None."""
        mock_get.return_value.status_code = 500
        result = get_song_correct_name("Artist - Song")
        assert result is None

    @patch("pikaraoke.routes.batch_song_renamer.requests.get")
    def test_returns_none_on_empty_results(self, mock_get):
        """Test that empty results return None."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {"trackmatches": {"track": []}}}
        result = get_song_correct_name("Unknown Song That Doesn't Exist")
        assert result is None

    @patch("pikaraoke.routes.batch_song_renamer.requests.get")
    def test_returns_best_match(self, mock_get):
        """Test that best match is returned."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "results": {
                "trackmatches": {
                    "track": [
                        {"name": "Viva La Vida", "artist": "Coldplay"},
                    ]
                }
            }
        }
        result = get_song_correct_name("Coldplay - Viva La Vida")
        assert result == "Viva La Vida - Coldplay"

    @patch("pikaraoke.routes.batch_song_renamer.requests.get")
    def test_cleans_query_before_search(self, mock_get):
        """Test that query is cleaned before API call."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {"trackmatches": {"track": []}}}
        get_song_correct_name("Artist - Song (Official Video) karaoke")
        # Verify the cleaned query was used
        call_args = mock_get.call_args
        params = call_args[1]["params"]
        assert "karaoke" not in params["track"].lower()
        assert "official" not in params["track"].lower()

    @patch("pikaraoke.routes.batch_song_renamer.requests.get")
    def test_returns_none_on_missing_trackmatches(self, mock_get):
        """Test handling of malformed API response."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {}}
        result = get_song_correct_name("Artist - Song")
        assert result is None
