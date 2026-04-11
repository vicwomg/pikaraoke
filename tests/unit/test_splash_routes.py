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


@pytest.fixture
def app():
    test_app = Flask(__name__)
    Babel(test_app)
    test_app.register_blueprint(splash_bp)
    return test_app


@pytest.fixture
def app_ctx(app):
    """Provide a Flask app context for tests that call helpers directly."""
    with app.app_context():
        yield


@pytest.fixture
def client(app):
    return app.test_client()


def _make_karaoke(low="", mid="", high=""):
    k = MagicMock()
    k.low_score_phrases = low
    k.mid_score_phrases = mid
    k.high_score_phrases = high
    return k


class TestDefaultScorePhrases:
    """Tests for _default_score_phrases()."""

    def test_returns_all_tiers(self, app_ctx):
        phrases = _default_score_phrases()
        assert set(phrases.keys()) == {"low", "mid", "high"}

    def test_each_tier_has_phrases(self, app_ctx):
        phrases = _default_score_phrases()
        for tier in ("low", "mid", "high"):
            assert len(phrases[tier]) > 0
            assert all(isinstance(p, str) for p in phrases[tier])


class TestGetActiveScorePhrases:
    """Tests for _get_active_score_phrases()."""

    def test_returns_defaults_when_no_custom_phrases(self, app_ctx):
        result = _get_active_score_phrases(_make_karaoke())
        assert result == _default_score_phrases()

    def test_returns_custom_phrases_with_pipe_separator(self, app_ctx):
        k = _make_karaoke(low="Bad|Terrible", mid="OK|Alright", high="Great|Amazing")
        result = _get_active_score_phrases(k)
        assert result["low"] == ["Bad", "Terrible"]
        assert result["mid"] == ["OK", "Alright"]
        assert result["high"] == ["Great", "Amazing"]

    def test_handles_legacy_newline_separator(self, app_ctx):
        result = _get_active_score_phrases(_make_karaoke(low="Bad\nTerrible"))
        assert result["low"] == ["Bad", "Terrible"]

    def test_falls_back_to_defaults_when_all_whitespace(self, app_ctx):
        result = _get_active_score_phrases(_make_karaoke(low="   |  |  "))
        assert result["low"] == _default_score_phrases()["low"]

    def test_mixed_custom_and_default(self, app_ctx):
        result = _get_active_score_phrases(_make_karaoke(low="Custom low", high="Custom high"))
        defaults = _default_score_phrases()
        assert result["low"] == ["Custom low"]
        assert result["mid"] == defaults["mid"]
        assert result["high"] == ["Custom high"]


class TestScorePhrasesEndpoint:
    """Tests for GET /splash/score_phrases."""

    @patch("pikaraoke.routes.splash.get_karaoke_instance")
    def test_returns_json_with_all_tiers(self, mock_get_instance, client):
        mock_get_instance.return_value = _make_karaoke(low="Bad|Terrible")

        response = client.get("/splash/score_phrases")

        assert response.status_code == 200
        data = response.get_json()
        assert set(data.keys()) == {"low", "mid", "high"}
        assert data["low"] == ["Bad", "Terrible"]
