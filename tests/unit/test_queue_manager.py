"""Unit tests for QueueManager in isolation.

These tests verify the QueueManager class independently of the Karaoke class,
ensuring queue logic is correct and maintainable.
"""

from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.queue_manager import QueueManager


@pytest.fixture
def mock_callbacks():
    """Create mocked callback functions for QueueManager."""
    return {
        "get_now_playing_user": MagicMock(return_value=None),
        "filename_from_path": MagicMock(
            side_effect=lambda path, *args: path.split("/")[-1].split("---")[0]
        ),
        "log_and_send": MagicMock(),
        "get_available_songs": MagicMock(
            return_value=[
                "/songs/song1---abc.mp4",
                "/songs/song2---def.mp4",
                "/songs/song3---ghi.mp4",
            ]
        ),
        "update_now_playing_socket": MagicMock(),
        "skip": MagicMock(return_value=True),
    }


@pytest.fixture
def queue_manager(mock_callbacks):
    """Create a QueueManager instance with mocked dependencies."""
    return QueueManager(
        socketio=MagicMock(),
        get_limit_user_songs_by=lambda: 0,
        get_enable_fair_queue=lambda: False,
        **mock_callbacks,
    )


class TestQueueManagerInitialization:
    """Test QueueManager initialization."""

    def test_initializes_with_empty_queue(self, queue_manager):
        """QueueManager should start with an empty queue."""
        assert queue_manager.queue == []

    def test_stores_socketio_reference(self, mock_callbacks):
        """QueueManager should store the socketio instance."""
        socketio = MagicMock()
        qm = QueueManager(
            socketio=socketio,
            get_limit_user_songs_by=lambda: 0,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )
        assert qm.socketio == socketio


class TestQueueManagerEnqueue:
    """Test enqueue functionality."""

    def test_enqueue_adds_song_to_empty_queue(self, queue_manager):
        """Enqueuing a song should add it to the queue."""
        result = queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        assert isinstance(result, list)
        assert result[0] is True
        assert len(queue_manager.queue) == 1
        assert queue_manager.queue[0]["file"] == "/songs/test---abc.mp4"
        assert queue_manager.queue[0]["user"] == "User1"

    def test_enqueue_prevents_duplicates(self, queue_manager):
        """Enqueuing the same song twice should fail."""
        queue_manager.enqueue("/songs/test---abc.mp4", "User1")
        result = queue_manager.enqueue("/songs/test---abc.mp4", "User2")

        assert result is False
        assert len(queue_manager.queue) == 1

    def test_enqueue_respects_user_limit(self, mock_callbacks):
        """Enqueuing should fail when user limit is reached."""
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 2,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )

        qm.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        qm.enqueue("/songs/song2---def.mp4", "LimitedUser")
        result = qm.enqueue("/songs/song3---ghi.mp4", "LimitedUser")

        assert isinstance(result, list)
        assert result[0] is False
        assert len(qm.queue) == 2

    def test_enqueue_to_front_inserts_at_beginning(self, queue_manager):
        """Enqueuing with add_to_front should insert at position 0."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2", add_to_front=True)

        assert len(queue_manager.queue) == 2
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song1---abc.mp4"

    def test_enqueue_with_semitones(self, queue_manager):
        """Enqueuing should store the semitones value."""
        queue_manager.enqueue("/songs/test---abc.mp4", "User1", semitones=3)

        assert queue_manager.queue[0]["semitones"] == 3

    def test_enqueue_triggers_socket_update(self, queue_manager):
        """Enqueuing should emit queue_update event."""
        queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        queue_manager.socketio.emit.assert_called_with("queue_update", namespace="/")


class TestQueueManagerFairQueue:
    """Test fair queue algorithm."""

    def test_fair_queue_interleaves_users(self, mock_callbacks):
        """Fair queue should interleave songs from different users."""
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 0,
            get_enable_fair_queue=lambda: True,
            **mock_callbacks,
        )

        qm.enqueue("/songs/a1---aaa.mp4", "UserA")
        qm.enqueue("/songs/a2---bbb.mp4", "UserA")
        qm.enqueue("/songs/b1---ccc.mp4", "UserB")

        users = [item["user"] for item in qm.queue]
        assert users == ["UserA", "UserB", "UserA"]

    def test_fair_queue_handles_multiple_users(self, mock_callbacks):
        """Fair queue should work with multiple users adding multiple songs."""
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 0,
            get_enable_fair_queue=lambda: True,
            **mock_callbacks,
        )

        qm.enqueue("/songs/a1---aaa.mp4", "UserA")
        qm.enqueue("/songs/b1---bbb.mp4", "UserB")
        qm.enqueue("/songs/c1---ccc.mp4", "UserC")
        qm.enqueue("/songs/a2---ddd.mp4", "UserA")
        qm.enqueue("/songs/b2---eee.mp4", "UserB")

        users = [item["user"] for item in qm.queue]
        assert users == ["UserA", "UserB", "UserC", "UserA", "UserB"]


class TestQueueManagerEdit:
    """Test queue editing functionality."""

    def test_queue_edit_move_up(self, queue_manager):
        """Moving a song up should shift it forward in queue."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.queue_edit("song3---ghi.mp4", "up")

        assert result is True
        assert queue_manager.queue[1]["file"] == "/songs/song3---ghi.mp4"
        assert queue_manager.queue[2]["file"] == "/songs/song2---def.mp4"

    def test_queue_edit_move_down(self, queue_manager):
        """Moving a song down should shift it backward in queue."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.queue_edit("song1---abc.mp4", "down")

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song1---abc.mp4"

    def test_queue_edit_delete(self, queue_manager):
        """Deleting a song should remove it from queue."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        result = queue_manager.queue_edit("song1---abc.mp4", "delete")

        assert result is True
        assert len(queue_manager.queue) == 1
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"

    def test_queue_edit_nonexistent_song(self, queue_manager):
        """Editing a nonexistent song should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.queue_edit("nonexistent---xyz.mp4", "delete")

        assert result is False
        assert len(queue_manager.queue) == 1


class TestQueueManagerClear:
    """Test queue clearing."""

    def test_queue_clear_empties_queue(self, queue_manager):
        """Clearing the queue should remove all songs."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        queue_manager.queue_clear()

        assert len(queue_manager.queue) == 0

    def test_queue_clear_triggers_skip(self, mock_callbacks):
        """Clearing the queue should trigger skip callback."""
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 0,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )
        qm.enqueue("/songs/song1---abc.mp4", "User1")

        qm.queue_clear()

        mock_callbacks["skip"].assert_called_once_with(False)


class TestQueueManagerRandom:
    """Test random song addition."""

    def test_queue_add_random_adds_songs(self, queue_manager):
        """Adding random songs should populate the queue."""
        result = queue_manager.queue_add_random(2)

        assert result is True
        assert len(queue_manager.queue) == 2

    def test_queue_add_random_avoids_duplicates(self, queue_manager):
        """Random songs should not duplicate existing queue items."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        queue_manager.queue_add_random(2)

        files = [item["file"] for item in queue_manager.queue]
        assert "/songs/song1---abc.mp4" in files
        assert len(files) == 3
        assert len(set(files)) == 3  # No duplicates

    def test_queue_add_random_with_insufficient_songs(self, mock_callbacks):
        """Adding more random songs than available should return False."""
        mock_callbacks["get_available_songs"].return_value = [
            "/songs/song1---abc.mp4",
            "/songs/song2---def.mp4",
        ]
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 0,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )

        result = qm.queue_add_random(5)

        assert result is False
        assert len(qm.queue) == 2


class TestQueueManagerHelpers:
    """Test helper methods."""

    def test_is_song_in_queue_returns_true_for_queued_song(self, queue_manager):
        """is_song_in_queue should return True for queued songs."""
        queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        assert queue_manager.is_song_in_queue("/songs/test---abc.mp4") is True

    def test_is_song_in_queue_returns_false_for_new_song(self, queue_manager):
        """is_song_in_queue should return False for non-queued songs."""
        assert queue_manager.is_song_in_queue("/songs/test---abc.mp4") is False

    def test_is_user_limited_returns_false_for_pikaraoke_user(self, mock_callbacks):
        """Pikaraoke system user should never be limited."""
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 1,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )

        assert qm.is_user_limited("Pikaraoke") is False
        assert qm.is_user_limited("Randomizer") is False

    def test_is_user_limited_respects_limit(self, mock_callbacks):
        """is_user_limited should respect the configured limit."""
        mock_callbacks["get_now_playing_user"].return_value = None
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 2,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )

        qm.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        assert qm.is_user_limited("LimitedUser") is False

        qm.enqueue("/songs/song2---def.mp4", "LimitedUser")
        assert qm.is_user_limited("LimitedUser") is True

    def test_is_user_limited_includes_now_playing(self, mock_callbacks):
        """is_user_limited should count currently playing song."""
        mock_callbacks["get_now_playing_user"].return_value = "LimitedUser"
        qm = QueueManager(
            socketio=MagicMock(),
            get_limit_user_songs_by=lambda: 2,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )

        qm.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        assert qm.is_user_limited("LimitedUser") is True


class TestQueueManagerSocketIO:
    """Test SocketIO integration."""

    def test_update_queue_socket_emits_event(self, queue_manager):
        """update_queue_socket should emit queue_update event."""
        queue_manager.update_queue_socket()

        queue_manager.socketio.emit.assert_called_with("queue_update", namespace="/")

    def test_update_queue_socket_handles_none_socketio(self, mock_callbacks):
        """update_queue_socket should not crash with None socketio."""
        qm = QueueManager(
            socketio=None,
            get_limit_user_songs_by=lambda: 0,
            get_enable_fair_queue=lambda: False,
            **mock_callbacks,
        )

        # Should not raise an exception
        qm.update_queue_socket()
