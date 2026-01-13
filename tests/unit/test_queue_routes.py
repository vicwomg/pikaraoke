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
    app = Flask(__name__)
    app.register_blueprint(queue_bp)
    return app


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
