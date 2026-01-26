"""Tests for queue-related SocketIO event payloads.

These tests verify the exact data emitted to SocketIO when queue operations occur,
documenting the contract between backend and frontend for real-time updates.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def karaoke_with_socketio(mock_karaoke):
    """Create a MockKaraoke instance with mocked SocketIO."""
    mock_karaoke.socketio = MagicMock()
    return mock_karaoke


class TestQueueSocketEmissions:
    """Verify queue operations emit correct SocketIO events."""

    def test_update_queue_socket_emits_event(self, karaoke_with_socketio):
        """update_queue_socket emits 'queue_update' with no payload."""
        k = karaoke_with_socketio
        k.queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        k.socketio.emit.reset_mock()

        k.queue_manager.update_queue_socket()

        k.socketio.emit.assert_called_once_with("queue_update", namespace="/")

    def test_update_queue_socket_works_with_empty_queue(self, karaoke_with_socketio):
        """update_queue_socket emits regardless of queue state."""
        k = karaoke_with_socketio

        k.queue_manager.update_queue_socket()

        k.socketio.emit.assert_called_once_with("queue_update", namespace="/")

    def test_update_now_playing_socket_emits_state(self, karaoke_with_socketio):
        """update_now_playing_socket emits current playback state."""
        k = karaoke_with_socketio
        k.now_playing = "/songs/current---xyz.mp4"
        k.now_playing_user = "CurrentUser"
        k.now_playing_transpose = 2
        k.now_playing_duration = 180
        k.now_playing_position = 45
        k.is_paused = False

        k.update_now_playing_socket()

        k.socketio.emit.assert_called_once()
        event_name, payload = k.socketio.emit.call_args[0]
        namespace = k.socketio.emit.call_args[1]["namespace"]

        assert event_name == "now_playing"
        assert namespace == "/"
        assert payload["now_playing"] == "/songs/current---xyz.mp4"
        assert payload["now_playing_user"] == "CurrentUser"
        assert payload["now_playing_transpose"] == 2
        assert payload["now_playing_duration"] == 180
        assert payload["now_playing_position"] == 45
        assert payload["is_paused"] is False
        assert payload["volume"] == 0.85

    def test_update_now_playing_socket_includes_up_next(self, karaoke_with_socketio):
        """update_now_playing_socket includes first queued song as up_next."""
        k = karaoke_with_socketio
        k.now_playing = "/songs/current---xyz.mp4"
        k.queue_manager.enqueue("/songs/Next Song---aaa.mp4", "User1")
        k.queue_manager.enqueue("/songs/Another Song---bbb.mp4", "User2")
        k.socketio.emit.reset_mock()

        k.update_now_playing_socket()

        payload = k.socketio.emit.call_args[0][1]
        assert payload["up_next"] == "Next Song"
        assert payload["next_user"] == "User1"

    def test_enqueue_triggers_queue_socket_update(self, karaoke_with_socketio):
        """enqueue triggers queue_update event."""
        k = karaoke_with_socketio

        k.queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        queue_update_calls = [
            call for call in k.socketio.emit.call_args_list if call[0][0] == "queue_update"
        ]
        assert len(queue_update_calls) > 0

    def test_queue_edit_delete_triggers_socket_updates(self, karaoke_with_socketio):
        """queue_edit (delete) triggers both queue_update and now_playing events."""
        k = karaoke_with_socketio
        k.queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        k.queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        k.socketio.emit.reset_mock()

        k.queue_manager.queue_edit("song1---abc.mp4", "delete")

        event_names = [call[0][0] for call in k.socketio.emit.call_args_list]
        assert "queue_update" in event_names
        assert "now_playing" in event_names

    def test_queue_clear_triggers_socket_updates(self, karaoke_with_socketio):
        """queue_clear triggers both queue_update and now_playing events."""
        k = karaoke_with_socketio
        k.queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        k.socketio.emit.reset_mock()

        k.queue_manager.queue_clear()

        event_names = [call[0][0] for call in k.socketio.emit.call_args_list]
        assert "queue_update" in event_names
        assert "now_playing" in event_names
        assert len(k.queue_manager.queue) == 0


class TestSocketIOEventFormats:
    """Verify SocketIO event payload structure matches frontend expectations."""

    def test_queue_item_has_required_fields(self, karaoke_with_socketio):
        """Queue items contain fields required by frontend."""
        k = karaoke_with_socketio
        k.queue_manager.enqueue("/songs/Artist - Song---abc123.mp4", "TestUser", semitones=2)

        queue_item = k.queue_manager.queue[0]

        assert queue_item["file"] == "/songs/Artist - Song---abc123.mp4"
        assert queue_item["user"] == "TestUser"
        assert queue_item["title"] == "Artist - Song"
        assert queue_item["semitones"] == 2

    def test_now_playing_payload_has_required_fields(self, karaoke_with_socketio):
        """now_playing event payload contains all fields required by frontend."""
        k = karaoke_with_socketio
        k.now_playing = "/songs/Artist - Song---abc123.mp4"
        k.now_playing_user = "TestUser"
        k.now_playing_transpose = 0
        k.now_playing_duration = 240
        k.now_playing_position = 30
        k.now_playing_url = "https://youtube.com/watch?v=abc123"
        k.now_playing_subtitle_url = None
        k.is_paused = False
        k.queue_manager.enqueue("/songs/Next Song---def456.mp4", "NextUser")
        k.socketio.emit.reset_mock()

        k.update_now_playing_socket()

        payload = k.socketio.emit.call_args[0][1]

        required_fields = [
            "now_playing",
            "now_playing_user",
            "now_playing_transpose",
            "now_playing_duration",
            "now_playing_position",
            "now_playing_url",
            "now_playing_subtitle_url",
            "is_paused",
            "volume",
            "up_next",
            "next_user",
        ]
        for field in required_fields:
            assert field in payload, f"Missing required field: {field}"

        assert payload["up_next"] == "Next Song"
        assert payload["next_user"] == "NextUser"
