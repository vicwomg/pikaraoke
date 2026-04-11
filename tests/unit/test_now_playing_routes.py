"""Tests for now_playing routes - API contract tests for home/splash pages."""

import json
from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

# Monkeypatch werkzeug.__version__ for Flask compatibility if missing
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.now_playing import nowplaying_bp


@pytest.fixture
def app():
    """Create a Flask app for testing."""
    test_app = Flask(__name__)
    test_app.register_blueprint(nowplaying_bp)
    return test_app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestNowPlayingApiContract:
    """Tests that verify /now_playing returns expected data structure.

    The home page and splash screen depend on these fields being present.
    These tests prevent regressions where API changes break the frontend.
    """

    @patch("pikaraoke.routes.now_playing.get_karaoke_instance")
    def test_now_playing_returns_all_required_fields(self, mock_get_instance, client):
        """GET /now_playing must return all fields the frontend expects."""
        mock_karaoke = MagicMock()
        mock_karaoke.get_now_playing.return_value = {
            "now_playing": "Artist - Song",
            "now_playing_user": "TestUser",
            "now_playing_duration": 180,
            "now_playing_transpose": 0,
            "now_playing_url": "/stream/abc123",
            "now_playing_subtitle_url": None,
            "up_next": "Next Artist - Next Song",
            "next_user": "NextUser",
            "is_paused": False,
            "volume": 0.85,
        }
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/now_playing")

        assert response.status_code == 200
        data = json.loads(response.data)
        # Verify all required fields for home/splash page
        assert "now_playing" in data
        assert "now_playing_user" in data
        assert "now_playing_duration" in data
        assert "now_playing_transpose" in data
        assert "now_playing_url" in data
        assert "up_next" in data
        assert "next_user" in data
        assert "is_paused" in data
        assert "volume" in data

    @patch("pikaraoke.routes.now_playing.get_karaoke_instance")
    def test_now_playing_with_nothing_playing(self, mock_get_instance, client):
        """GET /now_playing with nothing playing returns null values correctly."""
        mock_karaoke = MagicMock()
        mock_karaoke.get_now_playing.return_value = {
            "now_playing": None,
            "now_playing_user": None,
            "now_playing_duration": 0,
            "now_playing_transpose": 0,
            "now_playing_url": None,
            "now_playing_subtitle_url": None,
            "up_next": None,
            "next_user": None,
            "is_paused": False,
            "volume": 0.85,
        }
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/now_playing")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["now_playing"] is None
        assert data["up_next"] is None

    @patch("pikaraoke.routes.now_playing.get_karaoke_instance")
    def test_now_playing_up_next_reflects_queue(self, mock_get_instance, client):
        """GET /now_playing up_next should reflect first item in queue."""
        mock_karaoke = MagicMock()
        mock_karaoke.get_now_playing.return_value = {
            "now_playing": "Current Song",
            "now_playing_user": "User1",
            "now_playing_duration": 200,
            "now_playing_transpose": 2,
            "now_playing_url": "/stream/current",
            "now_playing_subtitle_url": None,
            "up_next": "Queued Song",
            "next_user": "User2",
            "is_paused": False,
            "volume": 0.5,
        }
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/now_playing")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["up_next"] == "Queued Song"
        assert data["next_user"] == "User2"
