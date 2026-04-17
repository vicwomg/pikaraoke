"""Unit tests for pikaraoke.lib.music_metadata."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib import music_metadata
from pikaraoke.lib.music_metadata import (
    _normalize_title,
    resolve_metadata,
    search_itunes,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """iTunes cache is process-wide; reset between tests."""
    music_metadata._search_itunes_cached.cache_clear()
    yield
    music_metadata._search_itunes_cached.cache_clear()


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Eminem - Stan (Long Version) ft. Dido", "Eminem - Stan ft. Dido"),
            ("Queen - Bohemian Rhapsody (Official Video)", "Queen - Bohemian Rhapsody"),
            ("Artist - Song [Official Music Video]", "Artist - Song"),
            ("Artist - Song (Lyrics)", "Artist - Song"),
            ("Artist - Song [Audio]", "Artist - Song"),
            ("Artist - Topic", "Artist"),
            ("Simple Title", "Simple Title"),
            ("  extra   whitespace  ", "extra whitespace"),
            ("", ""),
            ("Artist - Song (Official) (Lyrics)", "Artist - Song"),
            ("Song (feat. X) (Official)", "Song"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert _normalize_title(raw) == expected

    def test_preserves_feat_without_parens(self):
        # iTunes is fuzzy enough that stripping feat would lose disambiguation.
        assert _normalize_title("Dr. Dre feat. Snoop Dogg - Nuthin") == (
            "Dr. Dre feat. Snoop Dogg - Nuthin"
        )


def _mock_itunes_response(results):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"resultCount": len(results), "results": results}
    return resp


class TestSearchItunes:
    def test_returns_artist_track_pairs(self):
        resp = _mock_itunes_response(
            [
                {"artistName": "Eminem", "trackName": "Stan (feat. Dido)"},
                {"artistName": "Eminem", "trackName": "Stan"},
            ]
        )
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert search_itunes("Eminem Stan", limit=2) == [
                {"artist": "Eminem", "track": "Stan (feat. Dido)"},
                {"artist": "Eminem", "track": "Stan"},
            ]

    def test_empty_query_short_circuits(self):
        with patch("pikaraoke.lib.music_metadata.requests.get") as mock_get:
            assert search_itunes("") == []
            mock_get.assert_not_called()

    def test_skips_entries_missing_artist_or_track(self):
        resp = _mock_itunes_response(
            [
                {"artistName": "", "trackName": "Song"},
                {"artistName": "Artist", "trackName": ""},
                {"artistName": "Artist", "trackName": "Song"},
            ]
        )
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert search_itunes("q") == [{"artist": "Artist", "track": "Song"}]

    def test_network_error_returns_empty(self):
        with patch(
            "pikaraoke.lib.music_metadata.requests.get",
            side_effect=requests.ConnectionError(),
        ):
            assert search_itunes("q") == []

    def test_non_200_returns_empty(self):
        resp = MagicMock(status_code=403)
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert search_itunes("q") == []

    def test_malformed_json_returns_empty(self):
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("bad json")
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert search_itunes("q") == []

    def test_missing_results_key_returns_empty(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert search_itunes("q") == []

    def test_caches_identical_queries(self):
        resp = _mock_itunes_response([{"artistName": "A", "trackName": "T"}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp) as mock_get:
            search_itunes("same query")
            search_itunes("same query")
            search_itunes("same query")
            assert mock_get.call_count == 1

    def test_different_limit_is_separate_cache_entry(self):
        resp = _mock_itunes_response([{"artistName": "A", "trackName": "T"}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp) as mock_get:
            search_itunes("q", limit=1)
            search_itunes("q", limit=5)
            assert mock_get.call_count == 2


class TestResolveMetadata:
    def test_returns_first_hit(self):
        resp = _mock_itunes_response([{"artistName": "Eminem", "trackName": "Stan"}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert resolve_metadata("Eminem - Stan (Long Version)") == {
                "artist": "Eminem",
                "track": "Stan",
            }

    def test_no_hits_returns_none(self):
        resp = _mock_itunes_response([])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert resolve_metadata("unknown title") is None

    def test_network_failure_returns_none(self):
        with patch(
            "pikaraoke.lib.music_metadata.requests.get",
            side_effect=requests.Timeout(),
        ):
            assert resolve_metadata("any title") is None

    def test_passes_normalized_query_to_itunes(self):
        resp = _mock_itunes_response([{"artistName": "Q", "trackName": "B"}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp) as mock_get:
            resolve_metadata("Queen - Bohemian Rhapsody (Official Video)")
            params = mock_get.call_args.kwargs["params"]
            assert params["term"] == "Queen - Bohemian Rhapsody"
            assert params["entity"] == "song"
            assert params["limit"] == 1
