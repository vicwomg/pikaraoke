"""Tests for splash routes — score phrase helpers and endpoint."""

from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask
from flask_babel import Babel

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.splash import (
    _default_score_phrases,
    _get_active_score_phrases,
    splash_bp,
)


def _create_app():
    """Create a Flask app with Babel for testing."""
    test_app = Flask(__name__)
    Babel(test_app)
    return test_app


@pytest.fixture
def app():
    test_app = _create_app()
    test_app.register_blueprint(splash_bp)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestDefaultScorePhrases:
    """Tests for _default_score_phrases()."""

    def test_returns_all_tiers(self):
        with _create_app().app_context():
            phrases = _default_score_phrases()
            assert set(phrases.keys()) == {"low", "mid", "high"}

    def test_each_tier_has_phrases(self):
        with _create_app().app_context():
            phrases = _default_score_phrases()
            for tier in ("low", "mid", "high"):
                assert len(phrases[tier]) > 0
                assert all(isinstance(p, str) for p in phrases[tier])


class TestGetActiveScorePhrases:
    """Tests for _get_active_score_phrases()."""

    def _make_karaoke(self, low="", mid="", high=""):
        k = MagicMock()
        k.low_score_phrases = low
        k.mid_score_phrases = mid
        k.high_score_phrases = high
        return k

    def test_returns_defaults_when_no_custom_phrases(self):
        with _create_app().app_context():
            result = _get_active_score_phrases(self._make_karaoke())
            defaults = _default_score_phrases()
            assert result == defaults

    def test_returns_custom_phrases_with_pipe_separator(self):
        k = self._make_karaoke(low="Bad|Terrible", mid="OK|Alright", high="Great|Amazing")
        with _create_app().app_context():
            result = _get_active_score_phrases(k)
            assert result["low"] == ["Bad", "Terrible"]
            assert result["mid"] == ["OK", "Alright"]
            assert result["high"] == ["Great", "Amazing"]

    def test_handles_legacy_newline_separator(self):
        k = self._make_karaoke(low="Bad\nTerrible")
        with _create_app().app_context():
            result = _get_active_score_phrases(k)
            assert result["low"] == ["Bad", "Terrible"]

    def test_falls_back_to_defaults_when_all_whitespace(self):
        k = self._make_karaoke(low="   |  |  ")
        with _create_app().app_context():
            result = _get_active_score_phrases(k)
            defaults = _default_score_phrases()
            assert result["low"] == defaults["low"]

    def test_mixed_custom_and_default(self):
        k = self._make_karaoke(low="Custom low", high="Custom high")
        with _create_app().app_context():
            result = _get_active_score_phrases(k)
            defaults = _default_score_phrases()
            assert result["low"] == ["Custom low"]
            assert result["mid"] == defaults["mid"]
            assert result["high"] == ["Custom high"]


class TestScorePhrasesEndpoint:
    """Tests for GET /splash/score_phrases."""

    @patch("pikaraoke.routes.splash.get_karaoke_instance")
    def test_returns_json_with_all_tiers(self, mock_get_instance, app, client):
        mock_k = MagicMock()
        mock_k.low_score_phrases = "Bad|Terrible"
        mock_k.mid_score_phrases = ""
        mock_k.high_score_phrases = ""
        mock_get_instance.return_value = mock_k

        response = client.get("/splash/score_phrases")

        assert response.status_code == 200
        data = response.get_json()
        assert set(data.keys()) == {"low", "mid", "high"}
        assert data["low"] == ["Bad", "Terrible"]
