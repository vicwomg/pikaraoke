"""Unit tests for Karaoke queue operations."""

import pytest


class TestEnqueue:
    """Tests for the enqueue method."""

    def test_enqueue_adds_song_to_queue(self, mock_karaoke):
        """Test that enqueue adds a song to the queue."""
        result = mock_karaoke.enqueue("/songs/test---abc123.mp4", "TestUser")

        assert len(mock_karaoke.queue) == 1
        assert mock_karaoke.queue[0]["file"] == "/songs/test---abc123.mp4"
        assert mock_karaoke.queue[0]["user"] == "TestUser"
        assert mock_karaoke.queue[0]["title"] == "test"
        assert mock_karaoke.queue[0]["semitones"] == 0
        assert result[0] is True

    def test_enqueue_with_semitones(self, mock_karaoke):
        """Test that enqueue respects semitones parameter."""
        mock_karaoke.enqueue("/songs/test---abc123.mp4", "TestUser", semitones=3)

        assert mock_karaoke.queue[0]["semitones"] == 3

    def test_enqueue_duplicate_song_rejected(self, mock_karaoke):
        """Test that the same song cannot be added twice."""
        mock_karaoke.enqueue("/songs/test---abc123.mp4", "User1")
        result = mock_karaoke.enqueue("/songs/test---abc123.mp4", "User2")

        assert result is False
        assert len(mock_karaoke.queue) == 1

    def test_enqueue_add_to_front(self, mock_karaoke):
        """Test that add_to_front puts song at position 0."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")
        mock_karaoke.enqueue("/songs/song3---ghi.mp4", "User3", add_to_front=True)

        assert mock_karaoke.queue[0]["file"] == "/songs/song3---ghi.mp4"
        assert len(mock_karaoke.queue) == 3

    def test_enqueue_user_limit_enforced(self, mock_karaoke):
        """Test that user song limit is enforced."""
        mock_karaoke.limit_user_songs_by = 2

        mock_karaoke.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "LimitedUser")
        result = mock_karaoke.enqueue("/songs/song3---ghi.mp4", "LimitedUser")

        assert result[0] is False
        assert len(mock_karaoke.queue) == 2

    def test_enqueue_user_limit_not_applied_to_pikaraoke(self, mock_karaoke):
        """Test that Pikaraoke user bypasses song limit."""
        mock_karaoke.limit_user_songs_by = 1

        mock_karaoke.enqueue("/songs/song1---abc.mp4", "Pikaraoke")
        result = mock_karaoke.enqueue("/songs/song2---def.mp4", "Pikaraoke")

        assert result[0] is True
        assert len(mock_karaoke.queue) == 2

    def test_enqueue_user_limit_not_applied_to_randomizer(self, mock_karaoke):
        """Test that Randomizer user bypasses song limit."""
        mock_karaoke.limit_user_songs_by = 1

        mock_karaoke.enqueue("/songs/song1---abc.mp4", "Randomizer")
        result = mock_karaoke.enqueue("/songs/song2---def.mp4", "Randomizer")

        assert result[0] is True
        assert len(mock_karaoke.queue) == 2


class TestQueueEdit:
    """Tests for the queue_edit method."""

    def test_queue_edit_move_up(self, mock_karaoke):
        """Test moving a song up in the queue."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")
        mock_karaoke.enqueue("/songs/song3---ghi.mp4", "User3")

        result = mock_karaoke.queue_edit("song3---ghi.mp4", "up")

        assert result is True
        assert mock_karaoke.queue[1]["file"] == "/songs/song3---ghi.mp4"

    def test_queue_edit_move_down(self, mock_karaoke):
        """Test moving a song down in the queue."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")
        mock_karaoke.enqueue("/songs/song3---ghi.mp4", "User3")

        result = mock_karaoke.queue_edit("song1---abc.mp4", "down")

        assert result is True
        assert mock_karaoke.queue[1]["file"] == "/songs/song1---abc.mp4"

    def test_queue_edit_delete(self, mock_karaoke):
        """Test deleting a song from the queue."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")

        result = mock_karaoke.queue_edit("song1---abc.mp4", "delete")

        assert result is True
        assert len(mock_karaoke.queue) == 1
        assert mock_karaoke.queue[0]["file"] == "/songs/song2---def.mp4"

    def test_queue_edit_move_up_first_song_fails(self, mock_karaoke):
        """Test that moving the first song up fails gracefully."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")

        result = mock_karaoke.queue_edit("song1---abc.mp4", "up")

        assert result is False
        assert mock_karaoke.queue[0]["file"] == "/songs/song1---abc.mp4"

    def test_queue_edit_move_down_last_song_fails(self, mock_karaoke):
        """Test that moving the last song down fails gracefully."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")

        result = mock_karaoke.queue_edit("song2---def.mp4", "down")

        assert result is False
        assert mock_karaoke.queue[1]["file"] == "/songs/song2---def.mp4"

    def test_queue_edit_nonexistent_song_fails(self, mock_karaoke):
        """Test that editing a non-existent song fails."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")

        result = mock_karaoke.queue_edit("nonexistent.mp4", "delete")

        assert result is False
        assert len(mock_karaoke.queue) == 1

    def test_queue_edit_invalid_action_fails(self, mock_karaoke):
        """Test that an invalid action fails."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")

        result = mock_karaoke.queue_edit("song1---abc.mp4", "invalid")

        assert result is False


class TestQueueAddRandom:
    """Tests for the queue_add_random method."""

    def test_queue_add_random_adds_songs(self, mock_karaoke_with_songs):
        """Test that random songs are added to the queue."""
        result = mock_karaoke_with_songs.queue_add_random(3)

        assert result is True
        assert len(mock_karaoke_with_songs.queue) == 3

    def test_queue_add_random_no_duplicates(self, mock_karaoke_with_songs):
        """Test that random doesn't add songs already in queue."""
        mock_karaoke_with_songs.enqueue("/songs/Artist - Song One---abc123.mp4", "User1")

        mock_karaoke_with_songs.queue_add_random(4)

        files = [item["file"] for item in mock_karaoke_with_songs.queue]
        assert len(files) == len(set(files))  # No duplicates

    def test_queue_add_random_empty_library_fails(self, mock_karaoke):
        """Test that adding random from empty library fails."""
        result = mock_karaoke.queue_add_random(3)

        assert result is False
        assert len(mock_karaoke.queue) == 0

    def test_queue_add_random_partial_when_not_enough_songs(self, mock_karaoke_with_songs):
        """Test that it adds what it can when requesting more than available."""
        result = mock_karaoke_with_songs.queue_add_random(10)

        assert result is False  # Returns False when ran out
        assert len(mock_karaoke_with_songs.queue) == 5  # Added all available


class TestQueueClear:
    """Tests for the queue_clear method."""

    def test_queue_clear_empties_queue(self, mock_karaoke):
        """Test that queue_clear removes all songs."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User2")

        mock_karaoke.queue_clear()

        assert len(mock_karaoke.queue) == 0


class TestIsSongInQueue:
    """Tests for the is_song_in_queue method."""

    def test_is_song_in_queue_true(self, mock_karaoke):
        """Test detection of song in queue."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")

        assert mock_karaoke.is_song_in_queue("/songs/song1---abc.mp4") is True

    def test_is_song_in_queue_false(self, mock_karaoke):
        """Test detection of song not in queue."""
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")

        assert mock_karaoke.is_song_in_queue("/songs/other---xyz.mp4") is False

    def test_is_song_in_queue_empty(self, mock_karaoke):
        """Test with empty queue."""
        assert mock_karaoke.is_song_in_queue("/songs/song1---abc.mp4") is False


class TestIsUserLimited:
    """Tests for the is_user_limited method."""

    def test_is_user_limited_disabled(self, mock_karaoke):
        """Test that limit of 0 means no limit."""
        mock_karaoke.limit_user_songs_by = 0
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User1")

        assert mock_karaoke.is_user_limited("User1") is False

    def test_is_user_limited_under_limit(self, mock_karaoke):
        """Test user under the limit."""
        mock_karaoke.limit_user_songs_by = 3
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")

        assert mock_karaoke.is_user_limited("User1") is False

    def test_is_user_limited_at_limit(self, mock_karaoke):
        """Test user at the limit."""
        mock_karaoke.limit_user_songs_by = 2
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")
        mock_karaoke.enqueue("/songs/song2---def.mp4", "User1")

        assert mock_karaoke.is_user_limited("User1") is True

    def test_is_user_limited_counts_now_playing(self, mock_karaoke):
        """Test that currently playing song counts toward limit."""
        mock_karaoke.limit_user_songs_by = 2
        mock_karaoke.now_playing_user = "User1"
        mock_karaoke.enqueue("/songs/song1---abc.mp4", "User1")

        assert mock_karaoke.is_user_limited("User1") is True
