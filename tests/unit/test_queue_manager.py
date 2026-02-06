"""Unit tests for QueueManager in isolation.

These tests verify the QueueManager class independently of the Karaoke class,
ensuring queue logic is correct and maintainable.
"""

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager


def extract_title(path: str, *args) -> str:
    """Extract title from song path for testing."""
    return path.split("/")[-1].split("---")[0]


@pytest.fixture
def events():
    """Create an EventSystem instance."""
    return EventSystem()


@pytest.fixture
def preferences(tmp_path):
    """Create a PreferenceManager with a temporary config file."""
    return PreferenceManager(config_file_path=str(tmp_path / "config.ini"))


@pytest.fixture
def queue_manager(preferences, events):
    """Create a QueueManager instance with real dependencies."""
    return QueueManager(
        preferences=preferences,
        events=events,
        get_now_playing_user=lambda: None,
        filename_from_path=extract_title,
        get_available_songs=lambda: [
            "/songs/song1---abc.mp4",
            "/songs/song2---def.mp4",
            "/songs/song3---ghi.mp4",
        ],
    )


class TestQueueManagerInitialization:
    """Test QueueManager initialization."""

    def test_initializes_with_empty_queue(self, queue_manager):
        """QueueManager should start with an empty queue."""
        assert queue_manager.queue == []


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

        assert isinstance(result, list)
        assert result[0] is False
        assert "already in" in result[1].lower()
        assert len(queue_manager.queue) == 1

    def test_enqueue_respects_user_limit(self, preferences, events):
        """Enqueuing should fail when user limit is reached."""
        preferences.set("limit_user_songs_by", 2)
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=extract_title,
            get_available_songs=lambda: [],
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

    def test_enqueue_emits_queue_update(self, queue_manager):
        """Enqueuing should emit queue_update event."""
        captured = []
        queue_manager._events.on("queue_update", lambda: captured.append(True))
        queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        assert len(captured) == 1

    def test_enqueue_emits_now_playing_update(self, queue_manager):
        """Enqueuing should emit now_playing_update event."""
        captured = []
        queue_manager._events.on("now_playing_update", lambda: captured.append(True))
        queue_manager.enqueue("/songs/test---abc.mp4", "User1")

        assert len(captured) == 1


class TestQueueManagerFairQueue:
    """Test fair queue algorithm."""

    def test_fair_queue_interleaves_users(self, preferences, events):
        """Fair queue should interleave songs from different users."""
        preferences.set("enable_fair_queue", True)
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=extract_title,
            get_available_songs=lambda: [],
        )

        qm.enqueue("/songs/a1---aaa.mp4", "UserA")
        qm.enqueue("/songs/a2---bbb.mp4", "UserA")
        qm.enqueue("/songs/b1---ccc.mp4", "UserB")

        users = [item["user"] for item in qm.queue]
        assert users == ["UserA", "UserB", "UserA"]

    def test_fair_queue_handles_multiple_users(self, preferences, events):
        """Fair queue should work with multiple users adding multiple songs."""
        preferences.set("enable_fair_queue", True)
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=extract_title,
            get_available_songs=lambda: [],
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

        result = queue_manager.queue_edit("/songs/song3---ghi.mp4", "up")

        assert result is True
        assert queue_manager.queue[1]["file"] == "/songs/song3---ghi.mp4"
        assert queue_manager.queue[2]["file"] == "/songs/song2---def.mp4"

    def test_queue_edit_move_down(self, queue_manager):
        """Moving a song down should shift it backward in queue."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.queue_edit("/songs/song1---abc.mp4", "down")

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song1---abc.mp4"

    def test_queue_edit_delete(self, queue_manager):
        """Deleting a song should remove it from queue."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        result = queue_manager.queue_edit("/songs/song1---abc.mp4", "delete")

        assert result is True
        assert len(queue_manager.queue) == 1
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"

    def test_queue_edit_nonexistent_song(self, queue_manager):
        """Editing a nonexistent song should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.queue_edit("/songs/nonexistent---xyz.mp4", "delete")

        assert result is False
        assert len(queue_manager.queue) == 1

    def test_queue_edit_requires_exact_match(self, queue_manager):
        """Queue edit should require exact path match, not partial match."""
        queue_manager.enqueue("/songs/love---abc.mp4", "User1")
        queue_manager.enqueue("/songs/love_shack---def.mp4", "User2")

        # Partial match should fail - "love" should not match "love_shack"
        result = queue_manager.queue_edit("love", "delete")
        assert result is False
        assert len(queue_manager.queue) == 2

        # Exact match should succeed
        result = queue_manager.queue_edit("/songs/love---abc.mp4", "delete")
        assert result is True
        assert len(queue_manager.queue) == 1
        assert queue_manager.queue[0]["file"] == "/songs/love_shack---def.mp4"

    def test_queue_edit_emits_events(self, queue_manager):
        """Successful queue edit should emit queue_update and now_playing_update."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        queue_updates = []
        now_playing_updates = []
        queue_manager._events.on("queue_update", lambda: queue_updates.append(True))
        queue_manager._events.on("now_playing_update", lambda: now_playing_updates.append(True))

        queue_manager.queue_edit("/songs/song1---abc.mp4", "delete")

        assert len(queue_updates) == 1
        assert len(now_playing_updates) == 1


class TestQueueManagerMoveToTopBottom:
    """Test move_to_top and move_to_bottom functionality."""

    def test_move_to_top_moves_song_to_front(self, queue_manager):
        """Moving a song to top should place it at index 0."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.move_to_top("/songs/song3---ghi.mp4")

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song3---ghi.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song1---abc.mp4"
        assert queue_manager.queue[2]["file"] == "/songs/song2---def.mp4"

    def test_move_to_top_fails_if_already_at_top(self, queue_manager):
        """Moving first song to top should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        result = queue_manager.move_to_top("/songs/song1---abc.mp4")

        assert result is False
        assert queue_manager.queue[0]["file"] == "/songs/song1---abc.mp4"

    def test_move_to_top_fails_if_not_found(self, queue_manager):
        """Moving nonexistent song to top should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.move_to_top("/songs/nonexistent---xyz.mp4")

        assert result is False

    def test_move_to_bottom_moves_song_to_end(self, queue_manager):
        """Moving a song to bottom should place it at last index."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.move_to_bottom("/songs/song1---abc.mp4")

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song3---ghi.mp4"
        assert queue_manager.queue[2]["file"] == "/songs/song1---abc.mp4"

    def test_move_to_bottom_fails_if_already_at_bottom(self, queue_manager):
        """Moving last song to bottom should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        result = queue_manager.move_to_bottom("/songs/song2---def.mp4")

        assert result is False
        assert queue_manager.queue[1]["file"] == "/songs/song2---def.mp4"

    def test_move_to_bottom_fails_if_not_found(self, queue_manager):
        """Moving nonexistent song to bottom should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.move_to_bottom("/songs/nonexistent---xyz.mp4")

        assert result is False

    def test_move_to_top_emits_events(self, queue_manager):
        """Successful move_to_top should emit queue_update and now_playing_update."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        queue_updates = []
        now_playing_updates = []
        queue_manager._events.on("queue_update", lambda: queue_updates.append(True))
        queue_manager._events.on("now_playing_update", lambda: now_playing_updates.append(True))

        queue_manager.move_to_top("/songs/song2---def.mp4")

        assert len(queue_updates) == 1
        assert len(now_playing_updates) == 1

    def test_move_to_bottom_emits_events(self, queue_manager):
        """Successful move_to_bottom should emit queue_update and now_playing_update."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        queue_updates = []
        now_playing_updates = []
        queue_manager._events.on("queue_update", lambda: queue_updates.append(True))
        queue_manager._events.on("now_playing_update", lambda: now_playing_updates.append(True))

        queue_manager.move_to_bottom("/songs/song1---abc.mp4")

        assert len(queue_updates) == 1
        assert len(now_playing_updates) == 1


class TestQueueManagerReorder:
    """Test queue reordering functionality."""

    def test_reorder_moves_song_forward(self, queue_manager):
        """Reordering should move a song from later position to earlier position."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.reorder(2, 0)

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song3---ghi.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song1---abc.mp4"
        assert queue_manager.queue[2]["file"] == "/songs/song2---def.mp4"

    def test_reorder_moves_song_backward(self, queue_manager):
        """Reordering should move a song from earlier position to later position."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")
        queue_manager.enqueue("/songs/song3---ghi.mp4", "User3")

        result = queue_manager.reorder(0, 2)

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song3---ghi.mp4"
        assert queue_manager.queue[2]["file"] == "/songs/song1---abc.mp4"

    def test_reorder_same_index_succeeds(self, queue_manager):
        """Reordering to the same index should succeed without changes."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        result = queue_manager.reorder(1, 1)

        assert result is True
        assert queue_manager.queue[0]["file"] == "/songs/song1---abc.mp4"
        assert queue_manager.queue[1]["file"] == "/songs/song2---def.mp4"

    def test_reorder_invalid_old_index(self, queue_manager):
        """Reordering with invalid old_index should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.reorder(5, 0)

        assert result is False
        assert len(queue_manager.queue) == 1

    def test_reorder_invalid_new_index(self, queue_manager):
        """Reordering with invalid new_index should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.reorder(0, 5)

        assert result is False
        assert len(queue_manager.queue) == 1

    def test_reorder_negative_index(self, queue_manager):
        """Reordering with negative index should return False."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        result = queue_manager.reorder(-1, 0)

        assert result is False

    def test_reorder_emits_events(self, queue_manager):
        """Successful reorder should emit queue_update and now_playing_update."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        queue_updates = []
        now_playing_updates = []
        queue_manager._events.on("queue_update", lambda: queue_updates.append(True))
        queue_manager._events.on("now_playing_update", lambda: now_playing_updates.append(True))

        queue_manager.reorder(1, 0)

        assert len(queue_updates) == 1
        assert len(now_playing_updates) == 1


class TestQueueManagerPopNext:
    """Test pop_next functionality."""

    def test_pop_next_returns_first_song(self, queue_manager):
        """Popping next should return the first song in queue."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        song = queue_manager.pop_next()

        assert song is not None
        assert song["file"] == "/songs/song1---abc.mp4"
        assert song["user"] == "User1"
        assert len(queue_manager.queue) == 1
        assert queue_manager.queue[0]["file"] == "/songs/song2---def.mp4"

    def test_pop_next_from_empty_queue(self, queue_manager):
        """Popping from empty queue should return None."""
        song = queue_manager.pop_next()

        assert song is None
        assert len(queue_manager.queue) == 0

    def test_pop_next_emits_queue_update(self, queue_manager):
        """Popping next should emit queue_update event."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        captured = []
        queue_manager._events.on("queue_update", lambda: captured.append(True))

        queue_manager.pop_next()

        assert len(captured) == 1

    def test_pop_next_preserves_song_data(self, queue_manager):
        """Popping next should preserve all song data."""
        queue_manager.enqueue("/songs/test---abc.mp4", "TestUser", semitones=5)

        song = queue_manager.pop_next()

        assert song is not None
        assert song["file"] == "/songs/test---abc.mp4"
        assert song["user"] == "TestUser"
        assert song["semitones"] == 5
        assert song["title"] == "test"


class TestQueueManagerClear:
    """Test queue clearing."""

    def test_queue_clear_empties_queue(self, queue_manager):
        """Clearing the queue should remove all songs."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        queue_manager.enqueue("/songs/song2---def.mp4", "User2")

        queue_manager.queue_clear()

        assert len(queue_manager.queue) == 0

    def test_queue_clear_emits_skip_requested(self, queue_manager):
        """Clearing the queue should emit skip_requested event."""
        captured = []
        queue_manager._events.on("skip_requested", lambda: captured.append(True))
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")

        queue_manager.queue_clear()

        assert len(captured) == 1

    def test_queue_clear_emits_queue_update(self, queue_manager):
        """Clearing the queue should emit queue_update event."""
        queue_manager.enqueue("/songs/song1---abc.mp4", "User1")
        captured = []
        queue_manager._events.on("queue_update", lambda: captured.append(True))

        queue_manager.queue_clear()

        assert len(captured) == 1


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

    def test_queue_add_random_with_insufficient_songs(self, preferences, events):
        """Adding more random songs than available should return False."""
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=extract_title,
            get_available_songs=lambda: [
                "/songs/song1---abc.mp4",
                "/songs/song2---def.mp4",
            ],
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

    def test_is_user_limited_returns_false_for_pikaraoke_user(self, preferences, events):
        """Pikaraoke system user should never be limited."""
        preferences.set("limit_user_songs_by", 1)
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=extract_title,
            get_available_songs=lambda: [],
        )

        assert qm.is_user_limited("Pikaraoke") is False
        assert qm.is_user_limited("Randomizer") is False

    def test_is_user_limited_respects_limit(self, preferences, events):
        """is_user_limited should respect the configured limit."""
        preferences.set("limit_user_songs_by", 2)
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: None,
            filename_from_path=extract_title,
            get_available_songs=lambda: [],
        )

        qm.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        assert qm.is_user_limited("LimitedUser") is False

        qm.enqueue("/songs/song2---def.mp4", "LimitedUser")
        assert qm.is_user_limited("LimitedUser") is True

    def test_is_user_limited_includes_now_playing(self, preferences, events):
        """is_user_limited should count currently playing song."""
        preferences.set("limit_user_songs_by", 2)
        qm = QueueManager(
            preferences=preferences,
            events=events,
            get_now_playing_user=lambda: "LimitedUser",
            filename_from_path=extract_title,
            get_available_songs=lambda: [],
        )

        qm.enqueue("/songs/song1---abc.mp4", "LimitedUser")
        assert qm.is_user_limited("LimitedUser") is True
