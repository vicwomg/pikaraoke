"""Pytest fixtures for PiKaraoke tests."""

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_manager import SongManager


class MockPlaybackController:
    """Minimal mock of PlaybackController for testing queue operations."""

    now_playing: str | None = None
    now_playing_filename: str | None = None
    now_playing_user: str | None = None
    now_playing_transpose: int = 0
    now_playing_duration: int | None = None
    now_playing_url: str | None = None
    now_playing_subtitle_url: str | None = None
    now_playing_position: float | None = None
    is_paused: bool = True
    is_playing: bool = False

    def skip(self, log_action: bool = True) -> bool:
        if self.is_playing:
            self.reset_now_playing()
            return True
        return False

    def pause(self) -> bool:
        if self.is_playing:
            self.is_paused = not self.is_paused
            return True
        return False

    def reset_now_playing(self) -> None:
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.now_playing_url = None
        self.now_playing_subtitle_url = None
        self.is_paused = True
        self.is_playing = False
        self.now_playing_transpose = 0
        self.now_playing_duration = None
        self.now_playing_position = None

    def get_now_playing(self) -> dict:
        return {
            "now_playing": self.now_playing,
            "now_playing_user": self.now_playing_user,
            "now_playing_duration": self.now_playing_duration,
            "now_playing_transpose": self.now_playing_transpose,
            "now_playing_url": self.now_playing_url,
            "now_playing_subtitle_url": self.now_playing_subtitle_url,
            "now_playing_position": self.now_playing_position,
            "is_paused": self.is_paused,
        }


class MockSongManager:
    """Minimal mock of SongManager for testing."""

    def __init__(self, songs=None):
        self.songs = MockSongList(songs)
        self.download_path = "/fake/path"

    filename_from_path = SongManager.filename_from_path


class MockKaraoke:
    """Minimal mock of the Karaoke class for testing queue operations.

    This mock isolates the queue logic from external dependencies like
    filesystem, network, subprocess (ffmpeg, yt-dlp), etc.
    """

    def __init__(self, tmp_path):
        self.song_manager = MockSongManager()
        self._socketio = None
        self.events = EventSystem()
        self.preferences = PreferenceManager(
            config_file_path=str(tmp_path / "config.ini"), target=self
        )
        self.playback_controller = MockPlaybackController()
        self.volume = 0.85
        self.running = True
        self.now_playing_notification = None

        # Set preferences that differ from defaults
        self.preferences.set("enable_fair_queue", True)

        # Wire event handlers (mirrors karaoke.py wiring)
        self.events.on("notification", self.log_and_send)
        self.events.on(
            "queue_update",
            lambda: self._socketio.emit("queue_update", namespace="/") if self._socketio else None,
        )
        self.events.on("now_playing_update", self.update_now_playing_socket)
        self.events.on("skip_requested", lambda: self.playback_controller.skip(False))

        # Initialize queue manager
        self.queue_manager = QueueManager(
            preferences=self.preferences,
            events=self.events,
            get_now_playing_user=lambda: self.playback_controller.now_playing_user,
            filename_from_path=SongManager.filename_from_path,
            get_available_songs=lambda: self.song_manager.songs,
        )

    @property
    def socketio(self):
        """Get the socketio instance."""
        return self._socketio

    @socketio.setter
    def socketio(self, value):
        """Set the socketio instance."""
        self._socketio = value

    # Import the actual methods we want to test
    from pikaraoke.karaoke import Karaoke

    # Bind the real methods to our mock class
    get_now_playing = Karaoke.get_now_playing
    reset_now_playing = Karaoke.reset_now_playing
    send_notification = Karaoke.send_notification
    log_and_send = Karaoke.log_and_send
    update_now_playing_socket = Karaoke.update_now_playing_socket
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
def mock_karaoke(tmp_path):
    """Create a MockKaraoke instance for testing."""
    return MockKaraoke(tmp_path)


@pytest.fixture
def mock_karaoke_with_songs(tmp_path):
    """Create a MockKaraoke instance with pre-populated songs."""
    k = MockKaraoke(tmp_path)
    songs = [
        "/songs/Artist - Song One---abc123.mp4",
        "/songs/Artist - Song Two---def456.mp4",
        "/songs/Artist - Song Three---ghi789.mp4",
        "/songs/Another Artist - Track---jkl012.mp4",
        "/songs/Band - Hit Song---mno345.mp4",
    ]
    k.song_manager = MockSongManager(songs)
    return k
