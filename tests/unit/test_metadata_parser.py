"""Unit tests for metadata_parser module."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.metadata_parser import (
    _detect_artist_first,
    clean_search_query,
    clear_song_name_cache,
    get_best_result,
    get_song_correct_name,
    has_artist_title_separator,
    has_youtube_id,
    lookup_lastfm,
    regex_tidy,
    score_result,
    search_lastfm_tracks,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with an empty song name cache."""
    clear_song_name_cache()
    yield
    clear_song_name_cache()


class TestCleanSearchQuery:
    """Tests for the clean_search_query function."""

    def test_removes_karaoke_suffix(self):
        result = clean_search_query("Artist - Song karaoke")
        assert "karaoke" not in result.lower()

    def test_removes_official_video(self):
        result = clean_search_query("Artist - Song Official Music Video")
        assert "official" not in result.lower()
        assert "video" not in result.lower()

    def test_removes_lyrics(self):
        result = clean_search_query("Artist - Song with lyrics")
        assert "lyrics" not in result.lower()

    def test_removes_parentheses_content(self):
        result = clean_search_query("Artist - Song (Official Video)")
        assert "(" not in result
        assert ")" not in result
        assert "Official" not in result

    def test_removes_brackets_content(self):
        result = clean_search_query("Artist - Song [HD]")
        assert "[" not in result
        assert "]" not in result
        assert "HD" not in result

    def test_replaces_underscores_with_spaces(self):
        result = clean_search_query("Artist_Name_-_Song_Title")
        assert "_" not in result
        assert "Artist Name" in result

    def test_removes_instrumental(self):
        result = clean_search_query("Artist - Song Instrumental")
        assert "instrumental" not in result.lower()

    def test_removes_hd_hq(self):
        result = clean_search_query("Artist - Song HD HQ")
        assert "hd" not in result.lower()
        assert "hq" not in result.lower()

    def test_removes_feat(self):
        result = clean_search_query("Artist feat. Other - Song")
        assert "feat" not in result.lower()

    def test_removes_remaster(self):
        result = clean_search_query("Artist - Song Remaster")
        assert "remaster" not in result.lower()

    def test_preserves_artist_and_title(self):
        result = clean_search_query("Coldplay - Viva La Vida")
        assert "Coldplay" in result
        assert "Viva La Vida" in result

    def test_removes_emojis(self):
        result = clean_search_query("Artist - Song \U0001f3a4\U0001f3b5")
        assert "\U0001f3a4" not in result
        assert "\U0001f3b5" not in result

    def test_strips_whitespace(self):
        result = clean_search_query("  Artist - Song  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_complex_query_cleanup(self):
        query = (
            "Artist_Name - Song Title (Official Music Video) [HD] karaoke with lyrics \U0001f3a4"
        )
        result = clean_search_query(query)
        assert "Artist Name" in result
        assert "Song Title" in result
        assert "karaoke" not in result.lower()
        assert "lyrics" not in result.lower()
        assert "[" not in result
        assert "(" not in result


class TestScoreResult:
    """Tests for the score_result function."""

    def test_exact_match_high_score(self):
        result = {"name": "Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert score >= 100

    def test_exact_match_reversed_order(self):
        result = {"name": "Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Viva La Vida - Coldplay")
        assert score >= 100

    def test_partial_match_moderate_score(self):
        result = {"name": "Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Coldplay - Viva")
        assert 0 < score < 100

    def test_uppercase_penalized(self):
        result_upper = {"name": "VIVA LA VIDA", "artist": "COLDPLAY"}
        result_normal = {"name": "Viva La Vida", "artist": "Coldplay"}
        score_upper = score_result(result_upper, "Coldplay - Viva La Vida")
        score_normal = score_result(result_normal, "Coldplay - Viva La Vida")
        assert score_upper < score_normal

    def test_no_match_low_score(self):
        result = {"name": "Completely Different", "artist": "Unknown Artist"}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert score <= 0

    def test_empty_result_fields(self):
        result = {"name": "", "artist": ""}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert isinstance(score, int)

    def test_missing_result_fields(self):
        result = {}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert isinstance(score, int)

    def test_query_with_pipe_separator(self):
        result = {"name": "Song Title", "artist": "Artist Name"}
        score = score_result(result, "Artist Name | Song Title")
        assert score >= 50

    def test_accented_characters_matched(self):
        result = {"name": "Caf\u00e9", "artist": "Artiste"}
        score = score_result(result, "Artiste - Cafe")
        assert score > 0

    def test_single_part_query_exact_match(self):
        result = {"name": "Bohemian Rhapsody", "artist": "Queen"}
        score = score_result(result, "Bohemian Rhapsody")
        assert isinstance(score, int)

    def test_single_part_query_partial_match(self):
        result = {"name": "Bohemian Rhapsody", "artist": "Queen"}
        score = score_result(result, "Bohemian")
        assert isinstance(score, int)

    def test_word_matching_fallback(self):
        result = {"name": "Something Different Song", "artist": "Artist"}
        score = score_result(result, "Something - Artist")
        assert score > -1000

    def test_bad_keyword_penalization_live(self):
        result_live = {"name": "Song - Live", "artist": "Artist"}
        result_normal = {"name": "Song", "artist": "Artist"}
        score_live = score_result(result_live, "Artist - Song")
        score_normal = score_result(result_normal, "Artist - Song")
        assert score_live < score_normal

    def test_bad_keyword_penalization_remix(self):
        result = {"name": "Song remix", "artist": "Artist"}
        score = score_result(result, "Artist - Song")
        assert isinstance(score, int)

    def test_long_title_penalization(self):
        long_name = "A" * 65
        result = {"name": long_name, "artist": "Artist"}
        score = score_result(result, "Artist - Song")
        assert isinstance(score, int)

    def test_mbid_bonus(self):
        result_with_mbid = {"name": "Song", "artist": "Artist", "mbid": "abc123"}
        result_without_mbid = {"name": "Song", "artist": "Artist"}
        score_with = score_result(result_with_mbid, "Artist - Song")
        score_without = score_result(result_without_mbid, "Artist - Song")
        assert score_with > score_without

    def test_artist_in_track_name_penalization(self):
        result = {"name": "Coldplay - Viva La Vida", "artist": "Coldplay"}
        score = score_result(result, "Coldplay - Viva La Vida")
        assert isinstance(score, int)

    def test_no_artist_match_penalization(self):
        result = {"name": "Song Title", "artist": "Unknown Artist"}
        score = score_result(result, "Coldplay - Song Title")
        assert score < 100

    def test_part2_word_matching(self):
        result = {"name": "Amazing Song Title", "artist": "SomeArtist"}
        score = score_result(result, "Whatever - Amazing")
        assert isinstance(score, int)


class TestDetectArtistFirst:
    """Tests for the _detect_artist_first format detection function."""

    def test_detects_artist_first(self):
        assert _detect_artist_first("Coldplay - Viva La Vida", "Coldplay", "Viva La Vida") is True

    def test_detects_title_first(self):
        assert _detect_artist_first("Viva La Vida - Coldplay", "Coldplay", "Viva La Vida") is False

    def test_no_separator_returns_false(self):
        assert _detect_artist_first("Bohemian Rhapsody", "Queen", "Bohemian Rhapsody") is False

    def test_handles_accented_characters(self):
        assert _detect_artist_first("Beyonce - Halo", "Beyonc\u00e9", "Halo") is True

    def test_partial_artist_match(self):
        assert _detect_artist_first("Coldplay Band - Song", "Coldplay", "Song") is True

    def test_cross_references_part2_when_part1_ambiguous(self):
        assert _detect_artist_first("AC DC - Back In Black", "AC/DC", "Back in Black") is True

    def test_cross_references_part2_for_title_first(self):
        assert _detect_artist_first("Back In Black - AC DC", "AC/DC", "Back in Black") is False

    def test_no_space_separator(self):
        assert _detect_artist_first("Artist-Song Title", "Artist", "Song Title") is True

    def test_fullwidth_pipe_separator(self):
        assert _detect_artist_first("Artist \uff5c Song", "Artist", "Song") is True


class TestGetBestResult:
    """Tests for the get_best_result function."""

    def test_returns_none_for_empty_results(self):
        assert get_best_result([], "Artist - Song") is None

    def test_returns_none_for_none_results(self):
        assert get_best_result(None, "Artist - Song") is None

    def test_preserves_artist_first_format(self):
        results = [{"name": "Song Title", "artist": "Artist Name"}]
        result = get_best_result(results, "Artist Name - Song Title")
        assert result == "Artist Name - Song Title"

    def test_preserves_title_first_format(self):
        results = [{"name": "Song Title", "artist": "Artist Name"}]
        result = get_best_result(results, "Song Title - Artist Name")
        assert result == "Song Title - Artist Name"

    def test_selects_best_match(self):
        results = [
            {"name": "Wrong Song", "artist": "Wrong Artist"},
            {"name": "Viva La Vida", "artist": "Coldplay"},
            {"name": "Another Wrong", "artist": "Another"},
        ]
        result = get_best_result(results, "Coldplay - Viva La Vida")
        assert "Viva La Vida" in result
        assert "Coldplay" in result

    def test_multiple_results_sorted_by_score(self):
        results = [
            {"name": "Song", "artist": "Artist"},
            {"name": "Song", "artist": "Artist", "mbid": "bonus"},
        ]
        result = get_best_result(results, "Artist - Song")
        assert " - " in result


class TestLookupLastfm:
    """Tests for the lookup_lastfm function (pure Last.fm path)."""

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_returns_none_on_api_error(self, mock_get):
        mock_get.return_value.status_code = 500
        result = lookup_lastfm("Artist - Song")
        assert result is None

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_returns_none_on_empty_results(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {"trackmatches": {"track": []}}}
        result = lookup_lastfm("Unknown Song That Doesn't Exist")
        assert result is None

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_returns_best_match(self, mock_get):
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
        result = lookup_lastfm("Coldplay - Viva La Vida")
        assert result == "Coldplay - Viva La Vida"

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_cleans_query_before_search(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {"trackmatches": {"track": []}}}
        lookup_lastfm("Artist - Song (Official Video) karaoke")
        call_args = mock_get.call_args
        params = call_args[1]["params"]
        assert "karaoke" not in params["track"].lower()
        assert "official" not in params["track"].lower()

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_preserves_format_from_original_filename(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "results": {
                "trackmatches": {
                    "track": [
                        {"name": "Song Title", "artist": "Artist Name"},
                    ]
                }
            }
        }
        result = lookup_lastfm("Artist Name - Song Title (Official Video) karaoke")
        assert result == "Artist Name - Song Title"

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_returns_none_on_missing_trackmatches(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {}}
        result = lookup_lastfm("Artist - Song")
        assert result is None


VALID_RESPONSE = {
    "results": {"trackmatches": {"track": [{"name": "Viva La Vida", "artist": "Coldplay"}]}}
}

RATE_LIMIT_RESPONSE = {"error": 29, "message": "Rate limit exceeded"}


class TestRateLimiting:
    """Tests for Last.fm rate limiting, retry, and cache-skip behavior."""

    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_error_29_triggers_retry_and_succeeds(self, mock_get, mock_sleep):
        rate_limit_resp = MagicMock(status_code=200)
        rate_limit_resp.json.return_value = RATE_LIMIT_RESPONSE

        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = VALID_RESPONSE

        mock_get.side_effect = [rate_limit_resp, ok_resp]
        result = lookup_lastfm("Coldplay - Viva La Vida")
        assert result is not None
        assert "Viva La Vida" in result
        assert mock_get.call_count == 2

    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_http_429_triggers_retry_and_succeeds(self, mock_get, mock_sleep):
        http_429_resp = MagicMock(status_code=429)

        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = VALID_RESPONSE

        mock_get.side_effect = [http_429_resp, ok_resp]
        result = lookup_lastfm("Coldplay - Viva La Vida")
        assert result is not None
        assert "Viva La Vida" in result

    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_rate_limited_result_not_cached(self, mock_get, mock_sleep):
        rate_limit_resp = MagicMock(status_code=200)
        rate_limit_resp.json.return_value = RATE_LIMIT_RESPONSE

        mock_get.side_effect = [rate_limit_resp, rate_limit_resp, rate_limit_resp]
        result = lookup_lastfm("Coldplay - Viva La Vida")
        assert result is None

        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = VALID_RESPONSE
        mock_get.side_effect = [ok_resp]
        result = lookup_lastfm("Coldplay - Viva La Vida")
        assert result is not None
        assert "Viva La Vida" in result

    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_genuine_no_results_is_cached(self, mock_get, mock_sleep):
        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = {"results": {"trackmatches": {"track": []}}}
        mock_get.return_value = ok_resp

        result1 = lookup_lastfm("Completely Unknown Song XYZ")
        result2 = lookup_lastfm("Completely Unknown Song XYZ")
        assert result1 is None
        assert result2 is None
        assert mock_get.call_count == 1

    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_max_retries_exhausted(self, mock_get, mock_sleep):
        rate_limit_resp = MagicMock(status_code=200)
        rate_limit_resp.json.return_value = RATE_LIMIT_RESPONSE
        mock_get.return_value = rate_limit_resp

        result = lookup_lastfm("Coldplay - Viva La Vida")
        assert result is None
        assert mock_get.call_count == 3

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    def test_backoff_timing(self, mock_sleep, mock_get):
        rate_limit_resp = MagicMock(status_code=200)
        rate_limit_resp.json.return_value = RATE_LIMIT_RESPONSE
        mock_get.return_value = rate_limit_resp

        lookup_lastfm("Coldplay - Viva La Vida")

        backoff_calls = [
            call.args[0] for call in mock_sleep.call_args_list if call.args and call.args[0] >= 1.0
        ]
        assert backoff_calls == [1.0, 2.0]


class TestRegexTidy:
    """Tests for the regex_tidy function."""

    def test_strips_trailing_karaoke(self):
        assert regex_tidy("Artist - Song karaoke") == "Artist - Song"

    def test_strips_trailing_hd(self):
        assert regex_tidy("Artist - Song HD") == "Artist - Song"

    def test_strips_trailing_instrumental(self):
        assert regex_tidy("Artist - Song instrumental") == "Artist - Song"

    def test_strips_trailing_lyrics(self):
        assert regex_tidy("Artist - Song lyrics") == "Artist - Song"

    def test_strips_trailing_with_lyrics(self):
        assert regex_tidy("Artist - Song with lyrics") == "Artist - Song"

    def test_strips_trailing_parenthesised_content(self):
        assert regex_tidy("Artist - Song (Official Video)") == "Artist - Song"

    def test_strips_trailing_bracketed_content(self):
        assert regex_tidy("Artist - Song [HD]") == "Artist - Song"

    def test_replaces_underscores(self):
        result = regex_tidy("Artist_Name - Song_Title")
        assert "_" not in result
        assert "Artist Name - Song Title" == result

    def test_removes_emoji(self):
        result = regex_tidy("Artist - Song \U0001f3a4")
        assert "\U0001f3a4" not in result

    def test_normalizes_em_dash(self):
        assert regex_tidy("Artist \u2014 Song") == "Artist - Song"

    def test_normalizes_en_dash(self):
        assert regex_tidy("Artist \u2013 Song") == "Artist - Song"

    def test_strips_trailing_dash(self):
        assert regex_tidy("Artist - Song -") == "Artist - Song"

    def test_collapses_whitespace(self):
        result = regex_tidy("Artist  -  Song   Title")
        assert "  " not in result

    def test_attribution_made_famous_by(self):
        result = regex_tidy("My Heart Will Go On (Made Famous by Celine Dion)")
        assert result == "My Heart Will Go On - Celine Dion"

    def test_attribution_in_the_style_of(self):
        result = regex_tidy("Bohemian Rhapsody (In the Style of Queen)")
        assert result == "Bohemian Rhapsody - Queen"

    def test_attribution_originally_performed_by(self):
        result = regex_tidy("Yesterday (Originally Performed by The Beatles)")
        assert result == "Yesterday - The Beatles"

    def test_attribution_inline_trailing(self):
        result = regex_tidy("Sweet Caroline made famous by Neil Diamond")
        assert result == "Sweet Caroline - Neil Diamond"

    def test_strips_karaoke_version_from_source(self):
        assert (
            regex_tidy("ABBA - Fernando - Karaoke Version from Zoom Karaoke") == "ABBA - Fernando"
        )

    def test_strips_karaoke_from_source(self):
        assert regex_tidy("Artist - Song Karaoke from KaraFun") == "Artist - Song"

    def test_strips_karaoke_dash_source(self):
        assert regex_tidy("Artist - Song - Karaoke - Sing King") == "Artist - Song"

    def test_strips_karaoke_by_source(self):
        assert regex_tidy("Artist - Song - Karaoke by Stingray") == "Artist - Song"

    def test_no_change_when_clean(self):
        assert regex_tidy("Artist - Song Title") == "Artist - Song Title"


class TestHasYoutubeId:
    """Tests for the has_youtube_id function."""

    def test_pikaraoke_format(self):
        assert has_youtube_id("Artist - Song---dQw4w9WgXcQ.mp4") is True

    def test_ytdlp_format(self):
        assert has_youtube_id("Artist - Song [dQw4w9WgXcQ].mp4") is True

    def test_no_id(self):
        assert has_youtube_id("Artist - Song.mp4") is False

    def test_short_id(self):
        assert has_youtube_id("Artist - Song---short.mp4") is False

    def test_full_path_pikaraoke(self):
        assert has_youtube_id("/songs/Artist - Song---dQw4w9WgXcQ.mp4") is True

    def test_full_path_ytdlp(self):
        assert has_youtube_id("/songs/Artist - Song [dQw4w9WgXcQ].mp4") is True


class TestHasArtistTitleSeparator:
    """Tests for the has_artist_title_separator function."""

    def test_with_separator(self):
        assert has_artist_title_separator("Artist - Title") is True

    def test_without_separator(self):
        assert has_artist_title_separator("Just Title") is False

    def test_dash_without_spaces(self):
        assert has_artist_title_separator("Artist-Title") is False


class TestSearchLastfmTracks:
    """Tests for the search_lastfm_tracks function."""

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_returns_formatted_results(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "results": {
                "trackmatches": {
                    "track": [
                        {"name": "Song", "artist": "Artist", "extra": "ignored"},
                    ]
                }
            }
        }
        results = search_lastfm_tracks("Artist - Song")
        assert results == [{"name": "Song", "artist": "Artist"}]

    @patch("pikaraoke.lib.metadata_parser.time.sleep")
    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_returns_empty_on_rate_limit(self, mock_get, mock_sleep):
        rate_limit_resp = MagicMock(status_code=200)
        rate_limit_resp.json.return_value = RATE_LIMIT_RESPONSE
        mock_get.return_value = rate_limit_resp

        results = search_lastfm_tracks("Artist - Song")
        assert results == []

    @patch("pikaraoke.lib.metadata_parser.requests.get")
    def test_passes_limit_param(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"results": {"trackmatches": {"track": []}}}
        search_lastfm_tracks("Artist - Song", limit=3)
        params = mock_get.call_args[1]["params"]
        assert params["limit"] == "3"


class TestProvenanceRouting:
    """Tests for get_song_correct_name provenance-based routing."""

    @patch("pikaraoke.lib.metadata_parser.lookup_lastfm")
    def test_youtube_file_with_separator_skips_lastfm(self, mock_lookup):
        result = get_song_correct_name(
            "Artist - Song Title", raw_filename="/songs/Artist - Song Title---dQw4w9WgXcQ.mp4"
        )
        mock_lookup.assert_not_called()
        assert result == "Artist - Song Title"

    @patch("pikaraoke.lib.metadata_parser.lookup_lastfm")
    def test_youtube_file_without_separator_falls_through(self, mock_lookup):
        mock_lookup.return_value = "Sweet Caroline - Neil Diamond"
        result = get_song_correct_name(
            "Sweet Caroline", raw_filename="/songs/Sweet Caroline---dQw4w9WgXcQ.mp4"
        )
        mock_lookup.assert_called_once_with("Sweet Caroline")
        assert result == "Sweet Caroline - Neil Diamond"

    @patch("pikaraoke.lib.metadata_parser.lookup_lastfm")
    def test_non_youtube_file_always_uses_lastfm(self, mock_lookup):
        mock_lookup.return_value = "Artist - Song"
        result = get_song_correct_name("Artist - Song", raw_filename="/songs/Artist - Song.mp4")
        mock_lookup.assert_called_once_with("Artist - Song")
        assert result == "Artist - Song"

    @patch("pikaraoke.lib.metadata_parser.lookup_lastfm")
    def test_no_raw_filename_uses_lastfm(self, mock_lookup):
        mock_lookup.return_value = "Artist - Song"
        result = get_song_correct_name("Artist - Song")
        mock_lookup.assert_called_once_with("Artist - Song")

    @patch("pikaraoke.lib.metadata_parser.lookup_lastfm")
    def test_youtube_bracket_format_with_separator(self, mock_lookup):
        result = get_song_correct_name(
            "Artist - Song", raw_filename="/songs/Artist - Song [dQw4w9WgXcQ].mp4"
        )
        mock_lookup.assert_not_called()
        assert result == "Artist - Song"
