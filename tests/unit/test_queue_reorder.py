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
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(queue_bp)
    # Mock Babel to avoid KeyError: 'babel'
    app.extensions["babel"] = MagicMock()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestQueueReorderSocketUpdates:
    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_reorder_updates_now_playing_socket(
        self, mock_gettext, mock_broadcast, mock_get_instance, mock_is_admin, client
    ):
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager.queue = [{"file": "song1"}, {"file": "song2"}]
        mock_get_instance.return_value = mock_karaoke

        response = client.post("/queue/reorder", data={"old_index": 0, "new_index": 1})

        assert response.status_code == 200
        assert json.loads(response.data)["success"] is True
        mock_karaoke.update_now_playing_socket.assert_called_once()

    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_queue_edit_top_updates_now_playing_socket(
        self, mock_gettext, mock_broadcast, mock_get_instance, mock_is_admin, client
    ):
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager.queue = [{"file": "song1"}, {"file": "song2"}]
        mock_karaoke.filename_from_path.return_value = "song2"
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/queue/edit?action=top&song=song2")

        assert response.status_code == 302  # Redirect
        mock_karaoke.update_now_playing_socket.assert_called_once()

    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_queue_edit_bottom_updates_now_playing_socket(
        self, mock_gettext, mock_broadcast, mock_get_instance, mock_is_admin, client
    ):
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager.queue = [{"file": "song1"}, {"file": "song2"}]
        mock_karaoke.filename_from_path.return_value = "song1"
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/queue/edit?action=bottom&song=song1")

        assert response.status_code == 302  # Redirect
        mock_karaoke.update_now_playing_socket.assert_called_once()
