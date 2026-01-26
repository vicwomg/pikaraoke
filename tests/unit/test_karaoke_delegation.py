"""Tests for Karaoke class queue_manager interface.

These tests verify that queue operations are accessible through the queue_manager.
"""

import pytest


class TestKaraokeQueueInterface:
    """Verify Karaoke class exposes queue methods with correct signatures."""

    def test_queue_attribute_is_list(self, mock_karaoke):
        """Karaoke.queue should be accessible as a list."""
        assert isinstance(mock_karaoke.queue_manager.queue, list)

    def test_enqueue_accepts_all_parameters(self, mock_karaoke):
        """enqueue() should accept file, user, semitones, and add_to_front."""
        result = mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")
        assert result is not False

        result = mock_karaoke.queue_manager.enqueue("/songs/test2---def.mp4", "User2", semitones=3)
        assert result is not False

        result = mock_karaoke.queue_manager.enqueue(
            "/songs/test3---ghi.mp4", "User3", add_to_front=True
        )
        assert result is not False

    def test_queue_edit_accepts_song_and_action(self, mock_karaoke):
        """queue_edit() should accept song filename and action."""
        mock_karaoke.queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = mock_karaoke.queue_manager.queue_edit("song1---abc.mp4", "delete")
        assert isinstance(result, bool)

    def test_queue_clear_empties_queue(self, mock_karaoke):
        """queue_clear() should remove all songs."""
        mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        mock_karaoke.queue_manager.queue_clear()
        assert len(mock_karaoke.queue_manager.queue) == 0

    def test_queue_add_random_accepts_amount(self, mock_karaoke):
        """queue_add_random() should accept amount and return bool."""
        result = mock_karaoke.queue_manager.queue_add_random(1)
        assert isinstance(result, bool)

    def test_is_song_in_queue_returns_bool(self, mock_karaoke):
        """is_song_in_queue() should return bool for given path."""
        mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        result = mock_karaoke.queue_manager.is_song_in_queue("/songs/test---abc.mp4")
        assert result is True

    def test_is_user_limited_returns_bool(self, mock_karaoke):
        """is_user_limited() should return bool for given username."""
        result = mock_karaoke.queue_manager.is_user_limited("TestUser")
        assert isinstance(result, bool)

    def test_update_queue_socket_is_callable(self, mock_karaoke):
        """update_queue_socket() should be callable without error."""
        mock_karaoke.queue_manager.update_queue_socket()


class TestKaraokeQueueBehavior:
    """Verify queue behavior remains consistent after refactoring."""

    def test_queue_modifications_persist(self, mock_karaoke):
        """Queue changes through Karaoke methods should persist."""
        mock_karaoke.queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        assert len(mock_karaoke.queue_manager.queue) == 2
        assert mock_karaoke.queue_manager.queue[0]["file"] == "/songs/song1---abc.mp4"

        mock_karaoke.queue_manager.queue_edit("song1---abc.mp4", "delete")

        assert len(mock_karaoke.queue_manager.queue) == 1
        assert mock_karaoke.queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"

    def test_enqueue_returns_list_on_success(self, mock_karaoke):
        """Successful enqueue returns [True, message]."""
        result = mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] is True
        assert isinstance(result[1], str)

    def test_enqueue_returns_false_for_duplicate(self, mock_karaoke):
        """Duplicate song enqueue returns False."""
        mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        result = mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")
        assert result is False

    def test_enqueue_returns_list_when_user_limited(self, mock_karaoke):
        """User limit rejection returns [False, message]."""
        mock_karaoke.limit_user_songs_by = 1
        mock_karaoke.queue_manager.enqueue("/songs/test1---abc.mp4", "User1")

        result = mock_karaoke.queue_manager.enqueue("/songs/test2---def.mp4", "User1")
        assert isinstance(result, list)
        assert result[0] is False
        assert isinstance(result[1], str)


class TestKaraokeQueueStateManagement:
    """Verify queue state attributes are properly accessible."""

    def test_enable_fair_queue_is_mutable(self, mock_karaoke):
        """enable_fair_queue attribute should be readable and writable."""
        original = mock_karaoke.enable_fair_queue
        mock_karaoke.enable_fair_queue = not original
        assert mock_karaoke.enable_fair_queue == (not original)

    def test_limit_user_songs_by_is_mutable(self, mock_karaoke):
        """limit_user_songs_by attribute should be readable and writable."""
        mock_karaoke.limit_user_songs_by = 5
        assert mock_karaoke.limit_user_songs_by == 5

    def test_socketio_can_be_assigned(self, mock_karaoke):
        """socketio attribute should accept assignment."""
        from unittest.mock import MagicMock

        mock_karaoke.socketio = MagicMock()
        assert mock_karaoke.socketio is not None


class TestKaraokeQueueIntegration:
    """Integration tests for complex queue operations."""

    def test_fair_queue_interleaves_users(self, mock_karaoke):
        """Fair queue should interleave songs from different users."""
        mock_karaoke.enable_fair_queue = True

        mock_karaoke.queue_manager.enqueue("/songs/a1---aaa.mp4", "UserA")
        mock_karaoke.queue_manager.enqueue("/songs/a2---bbb.mp4", "UserA")
        mock_karaoke.queue_manager.enqueue("/songs/b1---ccc.mp4", "UserB")

        users = [item["user"] for item in mock_karaoke.queue_manager.queue]
        assert users == ["UserA", "UserB", "UserA"]

    def test_user_limit_blocks_excess_songs(self, mock_karaoke):
        """User limit should reject songs beyond the limit."""
        mock_karaoke.limit_user_songs_by = 2

        mock_karaoke.queue_manager.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        mock_karaoke.queue_manager.enqueue("/songs/song2---def.mp4", "LimitedUser")
        result = mock_karaoke.queue_manager.enqueue("/songs/song3---ghi.mp4", "LimitedUser")

        assert result[0] is False
        assert len(mock_karaoke.queue_manager.queue) == 2

    def test_queue_operations_trigger_socket_updates(self, mock_karaoke):
        """Queue operations with SocketIO should trigger emit calls."""
        from unittest.mock import MagicMock

        mock_karaoke.socketio = MagicMock()

        mock_karaoke.queue_manager.enqueue("/songs/test---abc.mp4", "User1")
        assert mock_karaoke.socketio.emit.called

        mock_karaoke.socketio.emit.reset_mock()
        mock_karaoke.queue_manager.queue_clear()
        assert mock_karaoke.socketio.emit.called
