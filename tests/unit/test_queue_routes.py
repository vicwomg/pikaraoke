import json
from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

# Monkeypatch werkzeug.__version__ for Flask compatibility if missing
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.queue import queue_bp


@pytest.fixture
def app():
    """Create a Flask app for testing."""
    test_app = Flask(__name__)
    test_app.register_blueprint(queue_bp)
    return test_app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestQueueRoutes:
    """Tests for queue routes."""

    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    def test_get_current_downloads(self, mock_get_instance, client):
        """Test the get_current_downloads route."""
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke

        # Mock successful status return
        expected_status = {"active": {"title": "Test Song", "progress": 50}, "pending": []}
        mock_karaoke.download_manager.get_downloads_status.return_value = expected_status

        response = client.get("/queue/downloads")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == expected_status

    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    def test_delete_download_error(self, mock_get_instance, client):
        """Test the delete_download_error route."""
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke

        # Test successful deletion
        mock_karaoke.download_manager.remove_error.return_value = True
        response = client.delete("/queue/downloads/errors/123")
        assert response.status_code == 200
        assert json.loads(response.data)["success"] is True

        # Test not found
        mock_karaoke.download_manager.remove_error.return_value = False
        response = client.delete("/queue/downloads/errors/999")
        assert response.status_code == 404
        assert json.loads(response.data)["success"] is False


class TestQueueApiContract:
    """Tests that verify the queue API returns expected data structure.

    These tests prevent regressions where API changes break the frontend.
    The queue and home pages depend on these endpoints returning specific fields.
    """

    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    def test_get_queue_returns_required_fields(self, mock_get_instance, client):
        """GET /get_queue must return all fields the frontend expects."""
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager.queue = [
            {
                "user": "TestUser",
                "file": "/songs/Artist - Song---abc123.mp4",
                "title": "Artist - Song",
                "semitones": 0,
            }
        ]
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/get_queue")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 1
        # Verify all required fields are present
        item = data[0]
        assert "user" in item
        assert "file" in item
        assert "title" in item
        assert "semitones" in item

    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    def test_get_queue_empty_returns_empty_array(self, mock_get_instance, client):
        """GET /get_queue must return empty array when queue is empty."""
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager.queue = []
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/get_queue")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == []


class TestQueueEditSocketUpdates:
    """Tests that queue edit actions emit update_now_playing_socket.

    These tests prevent regressions where queue changes don't update the
    splash screen's "up next" display. See commit 7b3909a for the original fix.
    """

    @pytest.fixture
    def app_with_secret(self):
        """Create a Flask app with secret key for session support."""
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(queue_bp)
        app.extensions["babel"] = MagicMock()
        return app

    @pytest.fixture
    def client_with_session(self, app_with_secret):
        """Create a test client with session support."""
        return app_with_secret.test_client()

    @pytest.mark.parametrize(
        "action,song_param",
        [
            ("delete", "&song=/songs/song1.mp4"),
            ("up", "&song=/songs/song2.mp4"),
            ("down", "&song=/songs/song1.mp4"),
            ("clear", ""),
        ],
    )
    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_queue_edit_updates_now_playing_socket(
        self,
        mock_gettext,
        mock_broadcast,
        mock_get_instance,
        mock_is_admin,
        client_with_session,
        action,
        song_param,
    ):
        """Queue edit actions must emit update_now_playing_socket for splash screen."""
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager.queue = [
            {"file": "/songs/song1.mp4"},
            {"file": "/songs/song2.mp4"},
        ]
        mock_karaoke.queue_manager.queue_edit.return_value = True
        mock_karaoke.filename_from_path.return_value = "song"
        mock_get_instance.return_value = mock_karaoke

        response = client_with_session.get(f"/queue/edit?action={action}{song_param}")

        assert response.status_code == 302
        mock_karaoke.update_now_playing_socket.assert_called_once()
