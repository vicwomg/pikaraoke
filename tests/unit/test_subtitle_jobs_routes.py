"""Unit tests for ``POST /api/songs/subtitles/bulk`` (Phase 2)."""

import json
from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.subtitle_jobs import MAX_BULK, subtitle_jobs_bp


@pytest.fixture
def admin_client():
    test_app = Flask(__name__)
    test_app.register_blueprint(subtitle_jobs_bp)
    return test_app.test_client()


def _row(
    source: str,
    state: str,
    *,
    tier: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    next_retry_at: str | None = None,
) -> dict:
    """Fake sqlite3.Row-shaped dict — only named-key reads are used."""
    return {
        "source": source,
        "state": state,
        "tier": tier,
        "error_code": error_code,
        "error_message": error_message,
        "started_at": None,
        "finished_at": None,
        "attempt_count": 1,
        "next_retry_at": next_retry_at,
    }


class TestPostBulkAuth:
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=False)
    def test_non_admin_rejected(self, _admin, admin_client):
        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [1, 2]})
        assert resp.status_code == 403


class TestPostBulkValidation:
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_missing_body_returns_400(self, _admin, admin_client):
        resp = admin_client.post(
            "/api/songs/subtitles/bulk", data="", content_type="application/json"
        )
        assert resp.status_code == 400

    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_song_ids_not_a_list_returns_400(self, _admin, admin_client):
        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": "abc"})
        assert resp.status_code == 400

    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_song_ids_with_non_int_returns_400(self, _admin, admin_client):
        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [1, "two", 3]})
        assert resp.status_code == 400

    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_song_ids_with_bool_returns_400(self, _admin, admin_client):
        # Booleans are int subclasses in Python — reject them explicitly so
        # ``[true]`` doesn't quietly map to song_id=1.
        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [True, False]})
        assert resp.status_code == 400

    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_exceeds_max_returns_400(self, _admin, admin_client):
        resp = admin_client.post(
            "/api/songs/subtitles/bulk",
            json={"song_ids": list(range(MAX_BULK + 1))},
        )
        assert resp.status_code == 400

    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_empty_list_returns_empty_dict(self, _admin, admin_client):
        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": []})
        assert resp.status_code == 200
        assert json.loads(resp.data) == {}


class TestPostBulkResponseShape:
    @patch("pikaraoke.routes.subtitle_jobs.get_karaoke_instance")
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_existing_song_returns_translated_status_and_label(
        self, _admin, mock_get, admin_client
    ):
        k = MagicMock()
        k.db.get_subtitle_jobs_bulk.return_value = {
            42: [
                _row("lrclib-sync", "success", tier="word"),
                _row("spotify-sync", "failed", error_code="not_found"),
                _row("AI", "running"),
                _row("genius-sync", "rate_limited"),
            ]
        }
        mock_get.return_value = k

        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [42]})
        assert resp.status_code == 200
        body = json.loads(resp.data)
        sources = {s["source"]: s for s in body["42"]["sources"]}

        # state→status translation per JOB_STATE_TO_UI_STATUS.
        assert sources["lrclib-sync"]["status"] == "ready"
        assert sources["spotify-sync"]["status"] == "error"
        assert sources["AI"]["status"] == "downloading"
        assert sources["genius-sync"]["status"] == "error"

        # Raw DB state still surfaces for fine-grained branching.
        assert sources["lrclib-sync"]["state"] == "success"
        assert sources["genius-sync"]["state"] == "rate_limited"

        # Per-source labels embedded — picker does not need a separate fetch.
        assert sources["lrclib-sync"]["label"] == "LRCLib + sync"
        assert sources["spotify-sync"]["label"] == "Spotify + sync"

        # Error payload preserved for the tooltip.
        assert sources["spotify-sync"]["error_code"] == "not_found"

    @patch("pikaraoke.routes.subtitle_jobs.get_karaoke_instance")
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_pre_phase1_song_synthesizes_default_sources(self, _admin, mock_get, admin_client):
        k = MagicMock()
        # Empty bulk result for the song id — no orchestrator rows yet.
        k.db.get_subtitle_jobs_bulk.return_value = {}
        mock_get.return_value = k

        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [7]})
        assert resp.status_code == 200
        body = json.loads(resp.data)
        sources = body["7"]["sources"]
        assert len(sources) > 0
        # All synthesized rows are ``na`` and have the canonical labels.
        for s in sources:
            assert s["status"] == "na"
            assert s["label"]  # non-empty
            assert s["state"] is None

    @patch("pikaraoke.routes.subtitle_jobs.get_karaoke_instance")
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_partial_found_mixes_real_and_synth(self, _admin, mock_get, admin_client):
        k = MagicMock()
        k.db.get_subtitle_jobs_bulk.return_value = {
            1: [_row("lrclib", "success", tier="line")],
        }
        mock_get.return_value = k

        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [1, 2]})
        body = json.loads(resp.data)
        # song 1 has real data
        sources_1 = {s["source"]: s for s in body["1"]["sources"]}
        assert sources_1["lrclib"]["status"] == "ready"
        # song 2 has synthesized placeholders
        for s in body["2"]["sources"]:
            assert s["status"] == "na"

    @patch("pikaraoke.routes.subtitle_jobs.get_karaoke_instance")
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_duplicate_ids_dedupe_in_response(self, _admin, mock_get, admin_client):
        k = MagicMock()
        k.db.get_subtitle_jobs_bulk.return_value = {
            5: [_row("lrclib", "success")],
        }
        mock_get.return_value = k

        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [5, 5, 5]})
        assert resp.status_code == 200
        body = json.loads(resp.data)
        # Documented behaviour: duplicate ids in the request collapse to one
        # entry in the response. Phase 2.5 queue consumers must not rely on
        # multiplicity.
        assert list(body.keys()) == ["5"]

    @patch("pikaraoke.routes.subtitle_jobs.get_karaoke_instance")
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_xss_passthrough_in_error_message(self, _admin, mock_get, admin_client):
        # Server returns raw text; the picker is responsible for textContent
        # rendering. Verifying here that the route does not auto-escape or
        # mangle the payload — frontend tests cover the rendering side.
        payload = "<script>alert(1)</script>"
        k = MagicMock()
        k.db.get_subtitle_jobs_bulk.return_value = {
            9: [_row("AI", "failed", error_code="boom", error_message=payload)],
        }
        mock_get.return_value = k

        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [9]})
        body = json.loads(resp.data)
        assert body["9"]["sources"][0]["error_message"] == payload


class TestPostBulkBatchPath:
    @patch("pikaraoke.routes.subtitle_jobs.get_karaoke_instance")
    @patch("pikaraoke.routes.subtitle_jobs.is_admin", return_value=True)
    def test_uses_bulk_db_method_not_singular(self, _admin, mock_get, admin_client):
        k = MagicMock()
        k.db.get_subtitle_jobs_bulk.return_value = {
            1: [_row("lrclib", "success")],
            2: [_row("AI", "running")],
        }
        mock_get.return_value = k

        resp = admin_client.post("/api/songs/subtitles/bulk", json={"song_ids": [1, 2]})
        assert resp.status_code == 200
        # The bulk method is called exactly once; the singular get_subtitle_jobs
        # is not called at all (avoids N+1 SQL on Pi 3).
        assert k.db.get_subtitle_jobs_bulk.call_count == 1
        k.db.get_subtitle_jobs.assert_not_called()
