"""Unit tests for metadata_providers module."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.metadata_parser import sanitize_filename
from pikaraoke.lib.metadata_providers import (
    _ALREADY_CORRECT,
    _UNCONFIRMED_CEILING,
    ITUNES_MAX_RETRIES,
    ITunesProvider,
    _normalize_for_matching,
    _normalize_query_parts,
    _suggestion_score,
    get_provider,
    suggest_metadata,
)


def _score(result: dict, query: str, featuring: str = "") -> int:
    """Test helper: wraps _suggestion_score with pre-normalization."""
    parts = _normalize_query_parts(query)
    feat_norm = _normalize_for_matching(featuring) if featuring else ""
    return _suggestion_score(result, parts, feat_norm)


def _mock_provider(*results: tuple[str, str]) -> MagicMock:
    """A provider returning (artist, title) pairs as full result dicts."""
    provider = MagicMock()
    provider.search.return_value = [
        {"artist": artist, "title": title, "year": "2008", "genre": "Pop", "source": "itunes"}
        for artist, title in results
    ]
    return provider


def _anchored(name: str, artist: str, title: str, artist_first: bool = True) -> int:
    """The top suggestion's 0-100 score for a filename against one mocked result."""
    provider = _mock_provider((artist, title))
    return suggest_metadata(name, provider=provider, artist_first=artist_first)[0]["score"]


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
        # First call at t=100, second at t=100.5 (only 0.5s elapsed, needs 2s gap)
        mock_time.side_effect = [100.0, 100.0, 100.5, 100.5]
        provider.search("first")
        provider.search("second")
        # Should have slept for the remaining 1.5s
        mock_sleep.assert_called_once_with(pytest.approx(1.5, abs=0.1))

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_passes_country_param(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"resultCount": 0, "results": []}
        provider = ITunesProvider(country="JP")
        provider.search("test")
        params = mock_get.call_args[1]["params"]
        assert params["country"] == "JP"

    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_defaults_country_to_us(self, mock_get, provider):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"resultCount": 0, "results": []}
        provider.search("test")
        params = mock_get.call_args[1]["params"]
        assert params["country"] == "US"

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


class TestITunesProviderRetry:
    """Tests for ITunesProvider retry behavior."""

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_retries_on_timeout(self, mock_get, mock_sleep, provider):
        import requests as req

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = ITUNES_RESPONSE
        mock_get.side_effect = [req.exceptions.Timeout(), mock_response]
        results = provider.search("Queen", max_retries=1)
        assert len(results) == 2
        assert mock_get.call_count == 2

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_retries_on_retryable_status(self, mock_get, mock_sleep, provider):
        fail_response = MagicMock()
        fail_response.status_code = 503
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = ITUNES_RESPONSE
        mock_get.side_effect = [fail_response, ok_response]
        results = provider.search("Queen", max_retries=1)
        assert len(results) == 2

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_no_retry_on_non_retryable_status(self, mock_get, mock_sleep, provider):
        mock_get.return_value.status_code = 400
        results = provider.search("Queen", max_retries=3)
        assert results == []
        assert mock_get.call_count == 1

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_no_retry_on_connection_error(self, mock_get, mock_sleep, provider):
        import requests as req

        mock_get.side_effect = req.exceptions.ConnectionError()
        results = provider.search("Queen", max_retries=3)
        assert results == []
        assert mock_get.call_count == 1

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_gives_up_after_max_retries(self, mock_get, mock_sleep, provider):
        mock_get.return_value.status_code = 429
        results = provider.search("Queen", max_retries=2)
        assert results == []
        assert mock_get.call_count == 3  # initial + 2 retries

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_backoff_delay_increases(self, mock_get, mock_sleep, provider):
        mock_get.return_value.status_code = 503
        provider.search("Queen", max_retries=2)
        # _backoff is called with attempt 0 and 1; delay = 3.0 + 2.0^(attempt+1)
        backoff_sleeps = [
            c.args[0] for c in mock_sleep.call_args_list if c.args and c.args[0] > 3.0
        ]
        assert len(backoff_sleeps) == 2
        assert backoff_sleeps[1] > backoff_sleeps[0]

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_search_default_no_retries(self, mock_get, mock_sleep, provider):
        mock_get.return_value.status_code = 503
        results = provider.search("Queen")
        assert results == []
        assert mock_get.call_count == 1

    @patch("pikaraoke.lib.metadata_providers.time.sleep")
    @patch("pikaraoke.lib.metadata_providers.requests.get")
    def test_lookup_defaults_to_max_retries(self, mock_get, mock_sleep, provider):
        mock_get.return_value.status_code = 429
        provider.lookup("Queen", "Under Pressure")
        # initial + ITUNES_MAX_RETRIES retries
        assert mock_get.call_count == ITUNES_MAX_RETRIES + 1


class TestGetProvider:
    """Tests for get_provider factory."""

    @staticmethod
    def _make_prefs(metadata_provider="itunes", itunes_search_country="US"):
        prefs = MagicMock()
        store = {
            "metadata_provider": metadata_provider,
            "itunes_search_country": itunes_search_country,
        }
        prefs.get.side_effect = lambda key, default=None: store.get(key, default)
        return prefs

    def test_default_returns_itunes(self):
        provider = get_provider(self._make_prefs())
        assert isinstance(provider, ITunesProvider)

    def test_unknown_provider_falls_back_to_itunes(self, caplog):
        with caplog.at_level(logging.WARNING):
            provider = get_provider(self._make_prefs(metadata_provider="spotify"))
        assert isinstance(provider, ITunesProvider)
        assert "Unknown metadata provider" in caplog.text

    def test_passes_country_to_provider(self):
        provider = get_provider(self._make_prefs(itunes_search_country="JP"))
        assert provider.country == "JP"

    def test_defaults_country_to_us(self):
        provider = get_provider(self._make_prefs())
        assert provider.country == "US"

    def test_country_override_takes_precedence(self):
        provider = get_provider(self._make_prefs(itunes_search_country="GB"), country="JP")
        assert provider.country == "JP"


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

    def test_artist_first_sets_the_display_order(self):
        """The library's convention decides, not the order the file happens to use."""
        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            {"artist": "Queen", "title": "Bohemian Rhapsody", "year": "", "genre": "", "source": ""}
        ]

        artist_first = suggest_metadata(
            "Bohemian Rhapsody - Queen", provider=mock_provider, artist_first=True
        )
        title_first = suggest_metadata(
            "Queen - Bohemian Rhapsody", provider=mock_provider, artist_first=False
        )

        assert artist_first[0]["display"] == "Queen - Bohemian Rhapsody"
        assert title_first[0]["display"] == "Bohemian Rhapsody - Queen"

    def test_uses_provided_provider(self):
        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            {"artist": "X", "title": "Y", "year": "", "genre": "", "source": "custom"}
        ]
        results = suggest_metadata("X - Y", provider=mock_provider)
        assert results[0]["source"] == "custom"
        mock_provider.search.assert_called_once()


class TestNormalizeForMatching:
    """Tests for _normalize_for_matching edge cases."""

    def test_strips_commas(self):
        assert _normalize_for_matching("Commodores, The") == "commodores the"

    def test_collapses_dotted_acronyms(self):
        assert _normalize_for_matching("D.I.V.O.R.C.E.") == "divorce"

    def test_collapses_sos(self):
        assert _normalize_for_matching("S.O.S.") == "sos"

    def test_normalizes_ampersand_to_and(self):
        assert _normalize_for_matching("Simon & Garfunkel") == "simon and garfunkel"

    def test_stylized_exclamation_as_i(self):
        assert _normalize_for_matching("P!nk") == "pink"

    def test_stylized_dollar_as_s(self):
        assert _normalize_for_matching("Ke$ha") == "kesha"

    def test_leaves_normal_text_unchanged(self):
        assert _normalize_for_matching("Dolly Parton") == "dolly parton"


class TestSuggestionScoring:
    """Tests for _suggestion_score ranking edge cases."""

    def test_correct_artist_beats_artist_name_in_wrong_title(self):
        """'Dolly Parton - DIVORCE' should rank Dolly Parton's D.I.V.O.R.C.E.
        above a track that merely contains 'Dolly Parton' in its title."""
        correct = {"artist": "Dolly Parton", "title": "D.I.V.O.R.C.E.", "genre": "Country"}
        wrong = {
            "artist": "David Liebe Hart",
            "title": "Dolly Parton + Beer Cereal Divorce",
            "genre": "Comedy",
        }
        query = "Dolly Parton - DIVORCE"
        assert _score(correct, query) > _score(wrong, query)

    def test_comma_inverted_artist_matches(self):
        """'Commodores, The - Three Times A Lady' should rank The Commodores
        above Kenny Rogers."""
        correct = {"artist": "The Commodores", "title": "Three Times a Lady", "genre": "Soul"}
        wrong = {"artist": "Kenny Rogers", "title": "Three Times a Lady", "genre": "Country"}
        query = "Commodores, The - Three Times A Lady"
        assert _score(correct, query) > _score(wrong, query)

    def test_two_part_query_scores_higher_than_single_part(self):
        """Explicit separator should score higher than separator-less decomposition."""
        result = {"artist": "Queen", "title": "Bohemian Rhapsody", "genre": "Rock"}
        score = _score(result, "Queen Bohemian Rhapsody")
        score_two_part = _score(result, "Queen - Bohemian Rhapsody")
        assert score_two_part > score

    def test_single_part_query_prefers_matching_artist(self):
        """'CAKE I Will Survive' should rank CAKE above Gloria Gaynor."""
        cake = {"artist": "CAKE", "title": "I Will Survive", "genre": "Rock"}
        gloria = {"artist": "Gloria Gaynor", "title": "I Will Survive", "genre": "Pop"}
        query = "CAKE I Will Survive"
        assert _score(cake, query) > _score(gloria, query)

    def test_featuring_with_ampersand_matches_and(self):
        """'Ft Sia And Fetty Wap' should prefer result with both featured artists."""
        both = {
            "artist": "David Guetta",
            "title": "Bang My Head (feat. Sia & Fetty Wap)",
            "genre": "Dance",
        }
        one = {
            "artist": "David Guetta",
            "title": "Bang My Head (feat. Sia)",
            "genre": "Dance",
        }
        query = "David Guetta - Bang My Head"
        featuring = "Sia And Fetty Wap"
        assert _score(both, query, featuring) > _score(one, query, featuring)

    def test_collaborator_bonus_ignores_which_part_is_first(self):
        """The bonus used to read part one as the artist, so it never fired on
        a 'Title - Artist' library."""
        result = {
            "artist": "Taylor Swift",
            "title": "Everything Has Changed (feat. Ed Sheeran)",
            "genre": "Pop",
        }
        artist_first = "Taylor Swift and Ed Sheeran - Everything Has Changed"
        title_first = "Everything Has Changed - Taylor Swift and Ed Sheeran"
        assert _score(result, title_first) == _score(result, artist_first)

    def test_version_keyword_uses_word_boundaries(self):
        """'Oliver's Army' should not be penalized for containing 'live'."""
        result = {
            "artist": "Elvis Costello & The Attractions",
            "title": "Oliver's Army",
            "genre": "Rock",
        }
        query = "Elvis Costello & The Attractions - Olivers Army"
        score = _score(result, query)
        live_result = {
            "artist": "Elvis Costello & The Attractions",
            "title": "Oliver's Army (Live)",
            "genre": "Rock",
        }
        score_live = _score(live_result, query)
        assert score > score_live

    def test_version_keyword_still_penalizes_actual_live(self):
        """'Oliver's Army (Live)' should be penalized for 'live' as a standalone word."""
        result = {
            "artist": "Elvis Costello & The Attractions",
            "title": "Oliver's Army (Live)",
            "genre": "Rock",
        }
        query = "Elvis Costello & The Attractions - Olivers Army"
        score_live = _score(result, query)
        clean = {
            "artist": "Elvis Costello & The Attractions",
            "title": "Oliver's Army",
            "genre": "Rock",
        }
        score_clean = _score(clean, query)
        assert score_clean > score_live

    def test_stylized_artist_pink_matches_itunes(self):
        """'P!nk - Raise Your Glass' should match iTunes result with artist 'Pink'."""
        result = {"artist": "Pink", "title": "Raise Your Glass", "genre": "Pop"}
        query = "P!nk - Raise Your Glass"
        score = _score(result, query)
        assert score > 0

    def test_stylized_artist_kesha_matches_itunes(self):
        """'Ke$ha - TiK ToK' should match iTunes result with artist 'Kesha'."""
        result = {"artist": "Kesha", "title": "TiK ToK", "genre": "Pop"}
        query = "Ke$ha - TiK ToK"
        score = _score(result, query)
        assert score > 0

    def test_apostrophe_difference_still_scores_exact(self):
        """Query 'Olivers Army' (no apostrophe) should exact-match title 'Oliver's Army'."""
        result = {
            "artist": "Elvis Costello & The Attractions",
            "title": "Oliver's Army",
            "genre": "Rock",
        }
        no_apos = _score(result, "Elvis Costello & The Attractions - Olivers Army")
        with_apos = _score(result, "Elvis Costello & The Attractions - Oliver's Army")
        assert no_apos == with_apos

    def test_a_clean_confirmed_match_tallies_to_the_anchor(self):
        """Two exact parts plus the cross-field bonus must sum to exactly 100.

        The anchor is assigned rather than computed, so nothing else would catch
        a weight tweak silently detaching the tally from the value it anchors to.
        """
        result = {"artist": "Queen", "title": "Bohemian Rhapsody", "genre": "Rock"}
        assert _score(result, "Queen - Bohemian Rhapsody") == _ALREADY_CORRECT


class TestScoreAnchoring:
    """Tests for the 0-100 scale: 100 already correct, 98/95 confirmed, <= 94 not."""

    def test_a_byte_identical_name_scores_100(self):
        assert _anchored("Beyoncé - Halo", "Beyoncé", "Halo") == 100

    def test_a_missing_accent_is_one_correction(self):
        """100 is byte-equality, not a fuzzy match: the file still needs renaming."""
        assert _anchored("Beyonce - Halo", "Beyoncé", "Halo") == 98

    def test_wrong_case_alone_is_one_correction(self):
        assert _anchored("abba - waterloo", "ABBA", "Waterloo") == 98

    def test_order_is_measured_against_the_preference(self):
        """The same file is already correct or one correction away, depending
        only on which way round the library writes its names."""
        assert _anchored("Halo - Beyoncé", "Beyoncé", "Halo", artist_first=True) == 98
        assert _anchored("Halo - Beyoncé", "Beyoncé", "Halo", artist_first=False) == 100

    def test_corrections_compound(self):
        """Two or three kinds of correction share one score, below a single kind."""
        assert _anchored("halo - beyonce", "Beyoncé", "Halo") == 95
        assert _anchored("Halo - Beyonce (Official Video)", "Beyoncé", "Halo") == 95

    def test_noise_the_tidy_cannot_reach_never_confirms(self):
        """regex_tidy only strips *trailing* qualifiers, so noise attached to a
        leading title survives into the query and the fields never match."""
        assert _anchored("Halo (Official Video) - Beyonce", "Beyoncé", "Halo") <= 94

    def test_a_tidied_name_still_needs_renaming(self):
        """The tidy decides confidence; the raw stem decides whether there is
        anything to apply. Confirmed fields alone must not reach 100."""
        assert _anchored("Beyoncé - Halo (Official Video)", "Beyoncé", "Halo") == 98

    def test_the_variant_guard_ignores_karaoke(self):
        """'(Karaoke Version)' is this app's commonest noise token. Treating it
        as a recording variant would push most of a library out of the band."""
        assert _anchored("Beyoncé - Halo (Karaoke Version)", "Beyoncé", "Halo") == 98

    def test_a_stripped_recording_variant_leaves_the_band(self):
        """Renaming these would lose the distinction, and possibly collide with
        the studio recording already in the library."""
        assert _anchored("Beyoncé - Halo (Live)", "Beyoncé", "Halo") <= 94
        assert _anchored("ABBA - Fernando (Remastered 2010)", "ABBA", "Fernando") <= 94

    def test_a_title_whose_body_reads_as_a_variant_is_untouched(self):
        """The variant guard reads bracketed text only, never the title body."""
        assert _anchored("Wings - Live and Let Die", "Wings", "Live and Let Die") == 100

    def test_a_featuring_credit_reaches_the_band_and_is_restored(self):
        """The credit is stripped from the query so the fields can agree; the
        rename then writes iTunes' canonical form back."""
        provider = _mock_provider(("David Guetta", "Bang My Head (feat. Sia)"))
        top = suggest_metadata("David Guetta - Bang My Head ft Sia", provider=provider)[0]
        assert top["score"] >= 95
        assert top["display"] == "David Guetta - Bang My Head (feat. Sia)"

    def test_a_qualifier_that_is_not_a_credit_disqualifies(self):
        artist, title = "Taylor Swift", "Everything Has Changed"
        assert _anchored(f"{artist} - {title}", artist, f"{title} (Taylor's Version)") <= 94
        assert _anchored(f"{artist} - {title}", artist, f"{title} (Commentary)") <= 94

    def test_a_bracketed_variant_beside_a_credit_disqualifies(self):
        """_PAREN_NOT_FEAT sees round brackets only, so this reaches the
        variant regex running over the extracted qualifier text."""
        assert (
            _anchored("Mark Ronson - Uptown Funk", "Mark Ronson", "Uptown Funk (feat. B) [Live]")
            <= 94
        )

    def test_a_windows_illegal_character_still_reaches_100(self):
        """The comparison is against the sanitised proposal, so a file named as
        correctly as the filesystem permits is not demoted for the platform's sake."""
        on_disk = sanitize_filename("Rush - Song: The Sequel")
        assert _anchored(on_disk, "Rush", "Song: The Sequel") == 100

    def test_an_unconfirmed_tally_cannot_enter_the_band(self):
        """Bonuses stack past 100 without confirming anything, which is what the
        ceiling exists to catch."""
        query = "David Guetta and Fetty Wap - Bang My Head"
        result = {"artist": "David Guetta", "title": "Bang My Head (feat. Sia & Fetty Wap)"}
        assert _score(result, query, "Sia") > _UNCONFIRMED_CEILING
        assert (
            _anchored(
                "David Guetta and Fetty Wap - Bang My Head ft Sia",
                result["artist"],
                result["title"],
            )
            <= 94
        )

    def test_a_heavily_penalised_result_floors_at_zero(self):
        provider = _mock_provider(("Nobody At All", f"{'x' * 70} (Live Remix)"))
        provider.search.return_value[0]["genre"] = "Rock/Pop"
        assert suggest_metadata("Beyoncé - Halo", provider=provider)[0]["score"] == 0

    def test_scores_never_rise_down_the_list(self):
        """The badge sits beside a ranked list, so a demotion applied after the
        sort could show a 95 above a 98."""
        provider = _mock_provider(
            ("Beyoncé", "Halo"),
            ("Beyoncé", "Halo (Live)"),
            ("Unrelated Band", "Some Other Song"),
        )
        scores = [r["score"] for r in suggest_metadata("Beyoncé - Halo", provider=provider)]
        assert scores == sorted(scores, reverse=True)

    def test_two_different_cjk_titles_are_not_confirmed(self):
        """The renamer's old normaliser folded every pure-CJK title to '', so
        two unrelated ones compared equal. This normaliser keeps them apart."""
        assert _anchored("夜に駆ける - YOASOBI", "YOASOBI", "君の名は") <= 94
        assert _anchored("YOASOBI - 夜に駆ける", "YOASOBI", "夜に駆ける") == 100
