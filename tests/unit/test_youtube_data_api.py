"""Unit tests for pikaraoke.lib.youtube_data_api."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib.youtube_data_api import batch_caption_info


class TestBatchCaptionInfo:
    def test_empty_when_api_key_missing(self):
        assert batch_caption_info(["abcDEFghIJK"], "") == {}
        assert batch_caption_info(["abcDEFghIJK"], "   ") == {}

    def test_empty_when_no_ids(self):
        assert batch_caption_info([], "AIza-test-key") == {}

    def test_maps_caption_flag_to_bool(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "items": [
                {"id": "aaaaaaaaaaa", "contentDetails": {"caption": "true"}},
                {"id": "bbbbbbbbbbb", "contentDetails": {"caption": "false"}},
            ]
        }
        response.raise_for_status = MagicMock()
        with patch("pikaraoke.lib.youtube_data_api.requests.get", return_value=response):
            result = batch_caption_info(["aaaaaaaaaaa", "bbbbbbbbbbb"], "AIza-key")
        assert result == {"aaaaaaaaaaa": True, "bbbbbbbbbbb": False}

    def test_missing_ids_default_to_false(self):
        # YouTube returns fewer items than requested when an ID is deleted.
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "items": [{"id": "aaaaaaaaaaa", "contentDetails": {"caption": "true"}}]
        }
        response.raise_for_status = MagicMock()
        with patch("pikaraoke.lib.youtube_data_api.requests.get", return_value=response):
            result = batch_caption_info(["aaaaaaaaaaa", "deletedXXXXX"], "AIza-key")
        # Deleted video should still map (to False), not be missing from the dict —
        # that way the caller can pre-flag "definitely no captions" cards too.
        assert result == {"aaaaaaaaaaa": True, "deletedXXXXX": False}

    def test_dedupes_input_ids(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "items": [{"id": "aaaaaaaaaaa", "contentDetails": {"caption": "true"}}]
        }
        response.raise_for_status = MagicMock()
        with patch(
            "pikaraoke.lib.youtube_data_api.requests.get", return_value=response
        ) as mock_get:
            result = batch_caption_info(["aaaaaaaaaaa", "aaaaaaaaaaa", "aaaaaaaaaaa"], "AIza-key")
        # Only one API call despite three input IDs.
        assert mock_get.call_count == 1
        sent_params = mock_get.call_args.kwargs["params"]
        assert sent_params["id"] == "aaaaaaaaaaa"
        assert result == {"aaaaaaaaaaa": True}

    def test_batches_in_groups_of_50(self):
        # 55 IDs should split into two HTTP calls.
        ids = [f"id{num:09d}"[:11] for num in range(55)]
        # Ensure they're actually 11 chars each to stay realistic.
        assert all(len(i) == 11 for i in ids)

        def _mk_response(*args, **kwargs):
            batch_ids = kwargs["params"]["id"].split(",")
            response = MagicMock(status_code=200)
            response.json.return_value = {
                "items": [{"id": vid, "contentDetails": {"caption": "false"}} for vid in batch_ids]
            }
            response.raise_for_status = MagicMock()
            return response

        with patch(
            "pikaraoke.lib.youtube_data_api.requests.get", side_effect=_mk_response
        ) as mock_get:
            result = batch_caption_info(ids, "AIza-key")
        assert mock_get.call_count == 2
        assert len(result) == 55

    def test_falls_through_on_http_error(self):
        response = MagicMock(status_code=403)
        response.raise_for_status = MagicMock(side_effect=requests.HTTPError("403 quotaExceeded"))
        with patch("pikaraoke.lib.youtube_data_api.requests.get", return_value=response):
            # An HTTP error means we can't trust any results — return {}
            # so the caller falls through to yt-dlp probes.
            result = batch_caption_info(["aaaaaaaaaaa"], "AIza-key")
        assert result == {}

    def test_falls_through_on_timeout(self):
        with patch(
            "pikaraoke.lib.youtube_data_api.requests.get",
            side_effect=requests.Timeout("API timeout"),
        ):
            result = batch_caption_info(["aaaaaaaaaaa"], "AIza-key")
        assert result == {}

    def test_falls_through_on_invalid_json(self):
        response = MagicMock(status_code=200)
        response.json.side_effect = ValueError("not json")
        response.raise_for_status = MagicMock()
        with patch("pikaraoke.lib.youtube_data_api.requests.get", return_value=response):
            result = batch_caption_info(["aaaaaaaaaaa"], "AIza-key")
        assert result == {}

    def test_skips_empty_video_ids(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "items": [{"id": "aaaaaaaaaaa", "contentDetails": {"caption": "true"}}]
        }
        response.raise_for_status = MagicMock()
        with patch(
            "pikaraoke.lib.youtube_data_api.requests.get", return_value=response
        ) as mock_get:
            batch_caption_info(["", "aaaaaaaaaaa", None], "AIza-key")  # type: ignore[list-item]
        sent_params = mock_get.call_args.kwargs["params"]
        assert sent_params["id"] == "aaaaaaaaaaa"
