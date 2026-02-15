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


def _make_queue_item(n: int) -> dict:
    """Create a queue item dict for testing."""
    return {"file": f"/songs/song{n}.mp4", "title": f"Song {n}", "user": f"User{n}", "semitones": 0}


class TestQueueEditSocketUpdates:
    """Tests that queue edit actions call appropriate QueueManager methods.

    These tests prevent regressions where queue changes don't trigger updates.
    QueueManager methods emit events (queue_update, now_playing_update) which
    update the splash screen's "up next" display. See commit 7b3909a for context.
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

    @pytest.fixture
    def queue_env(self, tmp_path):
        """Create a QueueManager with event tracking and mock karaoke instance.

        Returns (queue_manager, mock_karaoke, queue_updates, now_playing_updates).
        """
        events = EventSystem()
        preferences = PreferenceManager(config_file_path=str(tmp_path / "config.ini"))
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=lambda path, *args: path.split("/")[-1],
            get_available_songs=lambda: [],
        )

        queue_updates = []
        now_playing_updates = []
        events.on("queue_update", lambda: queue_updates.append(True))
        events.on("now_playing_update", lambda: now_playing_updates.append(True))

        mock_karaoke = MagicMock()
        mock_karaoke.queue_manager = qm
        mock_karaoke.song_manager.filename_from_path.return_value = "song"

        return qm, mock_karaoke, queue_updates, now_playing_updates

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
    def test_queue_edit_emits_events(
        self,
        mock_gettext,
        mock_broadcast,
        mock_get_instance,
        mock_is_admin,
        client_with_session,
        action,
        song_param,
        queue_env,
    ):
        """Queue edit actions emit queue_update and now_playing_update events."""
        qm, mock_karaoke, queue_updates, now_playing_updates = queue_env
        qm.queue = [_make_queue_item(1), _make_queue_item(2)]
        mock_get_instance.return_value = mock_karaoke

        response = client_with_session.get(f"/queue/edit?action={action}{song_param}")

        assert response.status_code == 302
        assert len(queue_updates) >= 1, "queue_update event should be emitted"
        assert len(now_playing_updates) >= 1, "now_playing_update event should be emitted"

    @pytest.mark.parametrize(
        "action,song_param,expected_new_index",
        [
            ("top", "&song=/songs/song2.mp4", 0),
            ("bottom", "&song=/songs/song1.mp4", 2),
        ],
    )
    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    @patch("pikaraoke.routes.queue._", side_effect=lambda x: x)
    def test_queue_edit_top_bottom_emits_events(
        self,
        mock_gettext,
        mock_get_instance,
        mock_is_admin,
        client_with_session,
        action,
        song_param,
        expected_new_index,
        queue_env,
    ):
        """Top/Bottom actions use QueueManager.reorder() which emits events."""
        qm, mock_karaoke, queue_updates, now_playing_updates = queue_env
        qm.queue = [_make_queue_item(1), _make_queue_item(2), _make_queue_item(3)]
        mock_get_instance.return_value = mock_karaoke

        response = client_with_session.get(f"/queue/edit?action={action}{song_param}")

        assert response.status_code == 302
        assert qm.queue[expected_new_index]["file"] in song_param.split("=")[1]
        assert len(queue_updates) == 1, "queue_update event should be emitted once"
        assert len(now_playing_updates) == 1, "now_playing_update event should be emitted once"

    @patch("pikaraoke.routes.queue.is_admin", return_value=True)
    @patch("pikaraoke.routes.queue.get_karaoke_instance")
    def test_queue_reorder_drag_drop_emits_events(
        self, mock_get_instance, mock_is_admin, client_with_session, queue_env
    ):
        """Drag-and-drop reorder uses QueueManager.reorder() which emits events."""
        qm, mock_karaoke, queue_updates, now_playing_updates = queue_env
        qm.queue = [_make_queue_item(n) for n in range(1, 5)]
        mock_get_instance.return_value = mock_karaoke

        response = client_with_session.post(
            "/queue/reorder", data={"old_index": "1", "new_index": "3"}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert qm.queue[3]["file"] == "/songs/song2.mp4"
        assert len(queue_updates) == 1, "queue_update event should be emitted once"
        assert len(now_playing_updates) == 1, "now_playing_update event should be emitted once"
