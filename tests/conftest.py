"""Pytest fixtures for PiKaraoke tests."""

import pytest

from pikaraoke.lib.queue_manager import QueueManager


class MockKaraoke:
    """Minimal mock of the Karaoke class for testing queue operations.

    This mock isolates the queue logic from external dependencies like
    filesystem, network, subprocess (ffmpeg, yt-dlp), etc.
    """

    def __init__(self):
        self.available_songs = MockSongList()
        self._socketio = None
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.now_playing_url = None
        self.now_playing_subtitle_url = None
        self.now_playing_transpose = 0
        self.now_playing_duration = None
        self.now_playing_position = None
        self.is_paused = True
        self.is_playing = False
        self.volume = 0.85
        self.running = True
        self.now_playing_notification = None
        self.limit_user_songs_by = 0
        self.hide_notifications = True
        self.download_path = "/fake/path"
        self.enable_fair_queue = True

        # Initialize queue manager
        self.queue_manager = QueueManager(
            socketio=self._socketio,
            get_limit_user_songs_by=lambda: self.limit_user_songs_by,
            get_enable_fair_queue=lambda: self.enable_fair_queue,
            get_now_playing_user=lambda: self.now_playing_user,
            filename_from_path=self.filename_from_path,
            log_and_send=self.log_and_send,
            get_available_songs=lambda: self.available_songs,
            update_now_playing_socket=self.update_now_playing_socket,
            skip=self.skip,
        )

    @property
    def socketio(self):
        """Get the socketio instance."""
        return self._socketio

    @socketio.setter
    def socketio(self, value):
        """Set the socketio instance and sync with queue_manager."""
        self._socketio = value
        self.queue_manager.socketio = value

    # Import the actual methods we want to test
    from pikaraoke.karaoke import Karaoke

    # Bind the real methods to our mock class
    filename_from_path = Karaoke.filename_from_path
    _convert_preference_value = Karaoke._convert_preference_value
    is_file_playing = Karaoke.is_file_playing
    get_now_playing = Karaoke.get_now_playing
    reset_now_playing = Karaoke.reset_now_playing
    send_notification = Karaoke.send_notification
    log_and_send = Karaoke.log_and_send
    update_now_playing_socket = Karaoke.update_now_playing_socket
    skip = Karaoke.skip
    pause = Karaoke.pause
    volume_change = Karaoke.volume_change
    vol_up = Karaoke.vol_up
    vol_down = Karaoke.vol_down
    restart = Karaoke.restart
    stop = Karaoke.stop
    reset_now_playing_notification = Karaoke.reset_now_playing_notification


class MockSongList:
    """Minimal mock of SongList for testing."""

    def __init__(self, songs=None):
        self._songs = set(songs) if songs else set()

    def __contains__(self, item):
        return item in self._songs

    def __len__(self):
        return len(self._songs)

    def __iter__(self):
        return iter(sorted(self._songs))

    def add(self, song):
        self._songs.add(song)

    def remove(self, song):
        self._songs.discard(song)


@pytest.fixture
def mock_karaoke():
    """Create a MockKaraoke instance for testing."""
    return MockKaraoke()


@pytest.fixture
def mock_karaoke_with_songs():
    """Create a MockKaraoke instance with pre-populated songs."""
    k = MockKaraoke()
    songs = [
        "/songs/Artist - Song One---abc123.mp4",
        "/songs/Artist - Song Two---def456.mp4",
        "/songs/Artist - Song Three---ghi789.mp4",
        "/songs/Another Artist - Track---jkl012.mp4",
        "/songs/Band - Hit Song---mno345.mp4",
    ]
    k.available_songs = MockSongList(songs)
    return k
