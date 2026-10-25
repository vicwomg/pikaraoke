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

    @patch("pikaraoke.routes.controller.broadcast_event")
    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_defer_stops_the_player_only_when_the_song_moved(
        self, mock_get_instance, mock_broadcast, client
    ):
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke

        mock_karaoke.let_next_go_first.return_value = False
        assert json.loads(client.post("/api/defer").data) == {"success": False}
        mock_broadcast.assert_not_called()

        mock_karaoke.let_next_go_first.return_value = True
        assert json.loads(client.post("/api/defer").data) == {"success": True}
        mock_broadcast.assert_called_once_with("skip", "defer")

    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_play_arms_manual_start(self, mock_get_instance, client):
        """POST /api/play arms the waiting song without touching the player."""
        mock_karaoke = MagicMock()
        mock_get_instance.return_value = mock_karaoke

        response = client.post("/api/play")

        assert response.status_code == 200
        assert json.loads(response.data) == {"success": True}
        mock_karaoke.playback_controller.request_start.assert_called_once_with()

    @patch("pikaraoke.routes.controller.broadcast_event")
    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_vocals_toggle_stops_player_when_it_restarts_song(
        self, mock_get_instance, mock_broadcast, client
    ):
        mock_karaoke = MagicMock()
        mock_karaoke.vocals_toggle_restarts.return_value = True
        mock_get_instance.return_value = mock_karaoke

        response = client.post("/api/vocals/off")

        assert response.status_code == 200
        mock_broadcast.assert_called_once_with("skip", "vocals toggle")
        mock_karaoke.set_vocals.assert_called_once_with(False)

    @patch("pikaraoke.routes.controller.broadcast_event")
    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_vocals_toggle_leaves_player_alone_otherwise(
        self, mock_get_instance, mock_broadcast, client
    ):
        mock_karaoke = MagicMock()
        mock_karaoke.vocals_toggle_restarts.return_value = False
        mock_get_instance.return_value = mock_karaoke

        client.post("/api/vocals/on")

        mock_broadcast.assert_not_called()
        mock_karaoke.set_vocals.assert_called_once_with(True)

    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_vocals_toggle_rejects_unknown_state(self, mock_get_instance, client):
        response = client.post("/api/vocals/maybe")
        assert response.status_code == 400
        mock_get_instance.return_value.set_vocals.assert_not_called()

    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_lyrics_shift_moves_the_playing_songs_lyrics(self, mock_get_instance, client):
        mock_get_instance.return_value.shift_lyrics.return_value = True

        response = client.post("/api/lyrics/shift/-0.5")

        assert json.loads(response.data) == {"success": True}
        mock_get_instance.return_value.shift_lyrics.assert_called_once_with(-0.5)

    @pytest.mark.parametrize("seconds", ["abc", "0", "nan", "inf", "30"])
    @patch("pikaraoke.routes.controller.get_karaoke_instance")
    def test_lyrics_shift_rejects_bad_amounts(self, mock_get_instance, seconds, client):
        response = client.post(f"/api/lyrics/shift/{seconds}")
        assert response.status_code == 400
        mock_get_instance.return_value.shift_lyrics.assert_not_called()
