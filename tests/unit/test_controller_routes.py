import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from pikaraoke.routes.controller import controller_bp


@pytest.fixture
def app():
    """Create a Flask app for testing."""
    test_app = Flask(__name__)
    test_app.register_blueprint(controller_bp)
    return test_app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestControllerRoutes:
    """Tests for playback control routes."""

    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_play_arms_manual_start(self, mock_get_instance, client):
        """POST /api/play arms the waiting song without touching the player."""
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke

        response = client.post("/api/play")

        assert response.status_code == 200
        assert json.loads(response.data) == {"success": True}
        mock_karaoke.playback_controller.request_start.assert_called_once_with()
