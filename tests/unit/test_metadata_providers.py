"""Unit tests for metadata_providers module."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.metadata_providers import (
    ITunesProvider,
    get_provider,
    suggest_metadata,
)

ITUNES_RESPONSE = {
    "resultCount": 2,
    "results": [
        {
            "wrapperType": "track",
            "artistName": "Queen",
            "trackName": "Bohemian Rhapsody",
            "releaseDate": "1975-10-31T12:00:00Z",
            "primaryGenreName": "Rock",
        },
        {
            "wrapperType": "track",
            "artistName": "Queen",
            "trackName": "We Will Rock You",
            "releaseDate": "1977-10-07T12:00:00Z",
            "primaryGenreName": "Rock",
        },
    ],
}


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset the module-level rate limit timestamp between tests."""
    from pikaraoke.lib.metadata_providers import ITunesProvider

    ITunesProvider._last_request_time = 0.0
    yield
    ITunesProvider._last_request_time = 0.0


@pytest.fixture()
def provider():
    return ITunesProvider()


class TestITunesProvider:
    """Tests for ITunesProvider.search()."""

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_returns_structured_results(self, mock_get, provider):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = ITUNES_RESPONSE
        results = provider.search("Queen Bohemian Rhapsody")
        assert len(results) == 2
        assert results[0] == {
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "year": "1975",
            "genre": "Rock",
            "source": "itunes",
        }

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_returns_empty_on_http_error(self, mock_get, provider):
        mock_get.return_value.status_code = 500
        results = provider.search("Queen")
        assert results == []

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_returns_empty_on_timeout(self, mock_get, provider):
        import requests

        mock_get.side_effect = requests.exceptions.Timeout()
        results = provider.search("Queen")
        assert results == []

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_returns_empty_on_request_error(self, mock_get, provider):
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError()
        results = provider.search("Queen")
        assert results == []

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_filters_non_track_results(self, mock_get, provider):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "resultCount": 2,
            "results": [
                {
                    "wrapperType": "collection",
                    "artistName": "Queen",
                    "collectionName": "Greatest Hits",
                },
                {
                    "wrapperType": "track",
                    "artistName": "Queen",
                    "trackName": "Bohemian Rhapsody",
                    "releaseDate": "1975-10-31T12:00:00Z",
                    "primaryGenreName": "Rock",
                },
            ],
        }
        results = provider.search("Queen")
        assert len(results) == 1
        assert results[0]["title"] == "Bohemian Rhapsody"

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_returns_empty_on_invalid_json(self, mock_get, provider):
        import requests

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = requests.exceptions.JSONDecodeError(
            "err", "doc", 0
        )
        results = provider.search("Queen")
        assert results == []

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.time.time")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_rate_limiting_enforced(self, mock_get, mock_time, mock_sleep, provider):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"resultCount": 0, "results": []}
        # First call at t=100, second at t=101 (only 1s elapsed, needs 3s gap)
        mock_time.side_effect = [100.0, 100.0, 101.0, 101.0]
        provider.search("first")
        provider.search("second")
        # Should have slept for the remaining 2s
        mock_sleep.assert_called_once_with(pytest.approx(2.0, abs=0.1))

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_handles_missing_release_date(self, mock_get, provider):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "resultCount": 1,
            "results": [
                {
                    "wrapperType": "track",
                    "artistName": "Unknown",
                    "trackName": "Song",
                    "primaryGenreName": "Pop",
                },
            ],
        }
        results = provider.search("Unknown Song")
        assert results[0]["year"] == ""


class TestITunesProviderLookup:
    """Tests for ITunesProvider.lookup()."""

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_lookup_prefers_exact_artist_match(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "resultCount": 2,
            "results": [
                {
                    "wrapperType": "track",
                    "artistName": "Queen & David Bowie",
                    "trackName": "Under Pressure",
                    "releaseDate": "1981-01-01T00:00:00Z",
                    "primaryGenreName": "Rock",
                },
                {
                    "wrapperType": "track",
                    "artistName": "Queen",
                    "trackName": "Under Pressure",
                    "releaseDate": "1981-01-01T00:00:00Z",
                    "primaryGenreName": "Rock",
                },
            ],
        }
        provider = ITunesProvider()
        result = provider.lookup("Queen", "Under Pressure")
        assert result is not None
        assert result["artist"] == "Queen"

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_lookup_returns_first_when_no_exact_match(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "resultCount": 1,
            "results": [
                {
                    "wrapperType": "track",
                    "artistName": "Queen & David Bowie",
                    "trackName": "Under Pressure",
                    "releaseDate": "1981-01-01T00:00:00Z",
                    "primaryGenreName": "Rock",
                },
            ],
        }
        provider = ITunesProvider()
        result = provider.lookup("Queen", "Under Pressure")
        assert result is not None
        assert result["artist"] == "Queen & David Bowie"

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_lookup_returns_none_on_empty(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"resultCount": 0, "results": []}
        provider = ITunesProvider()
        result = provider.lookup("Nobody", "Nothing")
        assert result is None


class TestGetProvider:
    """Tests for get_provider factory."""

    def test_default_returns_itunes(self):
        prefs = MagicMock()
        prefs.get.return_value = "itunes"
        provider = get_provider(prefs)
        assert isinstance(provider, ITunesProvider)

    def test_unknown_provider_falls_back_to_itunes(self, caplog):
        prefs = MagicMock()
        prefs.get.return_value = "spotify"
        with caplog.at_level(logging.WARNING):
            provider = get_provider(prefs)
        assert isinstance(provider, ITunesProvider)
        assert "Unknown metadata provider" in caplog.text


class TestSuggestMetadata:
    """Tests for suggest_metadata pipeline."""

    def test_calls_regex_tidy_then_provider_search(self):
        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            {
                "artist": "Queen",
                "title": "Bohemian Rhapsody",
                "year": "1975",
                "genre": "Rock",
                "source": "itunes",
            }
        ]
        results = suggest_metadata("Queen - Bohemian Rhapsody karaoke", provider=mock_provider)
        # regex_tidy should strip "karaoke", so search receives cleaned input
        call_args = mock_provider.search.call_args
        assert "karaoke" not in call_args[0][0].lower()
        assert len(results) == 1

    def test_returns_empty_when_provider_returns_empty(self):
        mock_provider = MagicMock()
        mock_provider.search.return_value = []
        results = suggest_metadata("Unknown Song", provider=mock_provider)
        assert results == []

    def test_results_include_source_field(self):
        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            {"artist": "A", "title": "B", "year": "2020", "genre": "Pop", "source": "itunes"}
        ]
        results = suggest_metadata("A - B", provider=mock_provider)
        assert all("source" in r for r in results)

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_defaults_to_itunes_when_no_provider(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = ITUNES_RESPONSE
        results = suggest_metadata("Queen")
        assert len(results) > 0
        assert results[0]["source"] == "itunes"

    def test_uses_provided_provider(self):
        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            {"artist": "X", "title": "Y", "year": "", "genre": "", "source": "custom"}
        ]
        results = suggest_metadata("X - Y", provider=mock_provider)
        assert results[0]["source"] == "custom"
        mock_provider.search.assert_called_once()
