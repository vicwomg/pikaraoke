import json
from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

# Monkeypatch werkzeug.__version__ for Flask compatibility if missing
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
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
    @pytest.fixture
    def queue_with_events(self):
        """Create QueueManager with event tracking for now_playing_update."""
        events = EventSystem()
        preferences = PreferenceManager()
        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager = QueueManager(
            preferences=preferences,
            events=events,
            filename_from_path=lambda path, _: path.split("/")[-1] if "/" in path else path,
        )
        mock_karaoke.queue_manager.queue = [{"file": "song1"}, {"file": "song2"}]
        events.on("now_playing_update", mock_karaoke.update_now_playing_socket)
        return mock_karaoke

    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_reorder_updates_now_playing_socket(
        self,
        mock_gettext,
        mock_broadcast,
        mock_get_instance,
        mock_is_admin,
        client,
        queue_with_events,
    ):
        mock_get_instance.return_value = queue_with_events

        response = client.post("/queue/reorder", data={"old_index": 0, "new_index": 1})

        assert response.status_code == 200
        assert json.loads(response.data)["success"] is True
        queue_with_events.update_now_playing_socket.assert_called_once()

    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_queue_edit_top_updates_now_playing_socket(
        self,
        mock_gettext,
        mock_broadcast,
        mock_get_instance,
        mock_is_admin,
        client,
        queue_with_events,
    ):
        queue_with_events.song_manager.filename_from_path.return_value = "song2"
        mock_get_instance.return_value = queue_with_events

        response = client.get("/queue/edit?action=top&song=song2")

        assert response.status_code == 302
        queue_with_events.update_now_playing_socket.assert_called_once()

    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue.broadcast_event")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_queue_edit_bottom_updates_now_playing_socket(
        self,
        mock_gettext,
        mock_broadcast,
        mock_get_instance,
        mock_is_admin,
        client,
        queue_with_events,
    ):
        queue_with_events.song_manager.filename_from_path.return_value = "song1"
        mock_get_instance.return_value = queue_with_events

        response = client.get("/queue/edit?action=bottom&song=song1")

        assert response.status_code == 302
        queue_with_events.update_now_playing_socket.assert_called_once()
