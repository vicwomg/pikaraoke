"""Unit tests for pikaraoke.lib.music_metadata."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib import music_metadata
from pikaraoke.lib.music_metadata import (
    _normalize_title,
    _upscale_artwork,
    fetch_itunes_track,
    fetch_musicbrainz_ids,
    resolve_metadata,
    search_itunes,
    search_itunes_full,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """iTunes + MusicBrainz caches are process-wide; reset between tests."""
    music_metadata._search_itunes_cached.cache_clear()
    music_metadata._search_musicbrainz_cached.cache_clear()
    yield
    music_metadata._search_itunes_cached.cache_clear()
    music_metadata._search_musicbrainz_cached.cache_clear()


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


class TestUpscaleArtwork:
    def test_rewrites_100x100_to_600x600(self):
        url = "https://is1.mzstatic.com/image/thumb/Music/.../100x100bb.jpg"
        assert _upscale_artwork(url) == (
            "https://is1.mzstatic.com/image/thumb/Music/.../600x600bb.jpg"
        )

    def test_custom_target(self):
        url = "https://cdn/foo/100x100.jpg"
        assert _upscale_artwork(url, target=300) == "https://cdn/foo/300x300bb.jpg"

    def test_non_matching_url_returned_unchanged(self):
        url = "https://cdn/foo/logo.png"
        assert _upscale_artwork(url) == url


class TestSearchItunesFull:
    def test_returns_all_extracted_fields(self):
        resp = _mock_itunes_response(
            [
                {
                    "artistName": "Eminem",
                    "trackName": "Stan",
                    "trackId": 12345,
                    "collectionName": "The Marshall Mathers LP",
                    "trackNumber": 3,
                    "releaseDate": "2000-05-23T07:00:00Z",
                    "artworkUrl100": "https://cdn/a/100x100bb.jpg",
                    "primaryGenreName": "Hip-Hop/Rap",
                }
            ]
        )
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            hits = search_itunes_full("Eminem Stan", limit=1)
        assert hits == [
            {
                "artistName": "Eminem",
                "trackName": "Stan",
                "trackId": 12345,
                "collectionName": "The Marshall Mathers LP",
                "trackNumber": 3,
                "releaseDate": "2000-05-23T07:00:00Z",
                "artworkUrl100": "https://cdn/a/100x100bb.jpg",
                "primaryGenreName": "Hip-Hop/Rap",
            }
        ]


class TestFetchItunesTrack:
    def test_returns_flat_enriched_shape(self):
        resp = _mock_itunes_response(
            [
                {
                    "artistName": "Eminem",
                    "trackName": "Stan",
                    "trackId": 42,
                    "collectionName": "MMLP",
                    "trackNumber": 3,
                    "releaseDate": "2000-05-23T07:00:00Z",
                    "artworkUrl100": "https://cdn/a/100x100bb.jpg",
                    "primaryGenreName": "Hip-Hop/Rap",
                }
            ]
        )
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            result = fetch_itunes_track("Eminem - Stan (Long Version)")
        assert result == {
            "itunes_id": "42",
            "artist": "Eminem",
            "track": "Stan",
            "album": "MMLP",
            "track_number": 3,
            "release_date": "2000-05-23T07:00:00Z",
            "cover_art_url": "https://cdn/a/600x600bb.jpg",
            "genre": "Hip-Hop/Rap",
        }

    def test_none_when_no_hits(self):
        resp = _mock_itunes_response([])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert fetch_itunes_track("unknown") is None


def _mock_mbrainz_response(recordings):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"recordings": recordings}
    return resp


class TestFetchMusicbrainzIds:
    def test_returns_mbid_and_isrc(self):
        resp = _mock_mbrainz_response(
            [
                {
                    "id": "rec-uuid",
                    "title": "Stan",
                    "isrcs": ["USRC17600001"],
                }
            ]
        )
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert fetch_musicbrainz_ids("Eminem", "Stan") == {
                "musicbrainz_recording_id": "rec-uuid",
                "isrc": "USRC17600001",
            }

    def test_returns_none_isrc_when_absent(self):
        resp = _mock_mbrainz_response([{"id": "rec-uuid", "title": "Stan"}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert fetch_musicbrainz_ids("Eminem", "Stan") == {
                "musicbrainz_recording_id": "rec-uuid",
                "isrc": None,
            }

    def test_none_when_no_recordings(self):
        resp = _mock_mbrainz_response([])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert fetch_musicbrainz_ids("A", "T") is None

    def test_none_when_recording_has_no_id(self):
        resp = _mock_mbrainz_response([{"title": "Stan"}])  # no "id"
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp):
            assert fetch_musicbrainz_ids("A", "T") is None

    def test_network_failure_returns_none(self):
        with patch(
            "pikaraoke.lib.music_metadata.requests.get",
            side_effect=requests.Timeout(),
        ):
            assert fetch_musicbrainz_ids("A", "T") is None

    def test_empty_inputs_short_circuit(self):
        with patch("pikaraoke.lib.music_metadata.requests.get") as mock_get:
            assert fetch_musicbrainz_ids("", "Stan") is None
            assert fetch_musicbrainz_ids("Eminem", "") is None
            mock_get.assert_not_called()

    def test_sends_user_agent(self):
        resp = _mock_mbrainz_response([{"id": "x", "isrcs": []}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp) as mock_get:
            fetch_musicbrainz_ids("A", "T")
            headers = mock_get.call_args.kwargs["headers"]
            assert "User-Agent" in headers
            assert "PiKaraoke" in headers["User-Agent"]

    def test_caches_identical_queries(self):
        resp = _mock_mbrainz_response([{"id": "x", "isrcs": []}])
        with patch("pikaraoke.lib.music_metadata.requests.get", return_value=resp) as mock_get:
            fetch_musicbrainz_ids("A", "T")
            fetch_musicbrainz_ids("A", "T")
            assert mock_get.call_count == 1
