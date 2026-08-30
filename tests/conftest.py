"""Pytest fixtures for PiKaraoke tests."""

import threading
from pathlib import Path, PurePath
from urllib.parse import quote

import pytest
from flask import Flask
from flask_babel import Babel

from pikaraoke.lib.auth import install_auth_gate, public
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_manager import SongManager

PIKARAOKE_PACKAGE = Path(__file__).resolve().parent.parent / "pikaraoke"


# What base.html itself url_for()s on every render, independent of which
# blueprint is under test. Listed here rather than in each caller so a new route
# test does not have to rediscover the layout's own dependencies.
_BASE_TEMPLATE_ENDPOINTS = [
    ("/", "home.home"),
    ("/queue", "queue.queue"),
    ("/browse", "files.browse"),
    ("/search", "search.search"),
    ("/info", "info.info"),
    ("/rankings", "sessions.rankings"),
    ("/history", "sessions.history"),
    ("/sessions", "sessions.sessions"),
    ("/api/sessions/singers", "sessions_api.get_singers"),
    ("/enqueue", "queue.enqueue_form"),
]


class StubAdminAuth:
    """Just enough of AdminAuth for is_admin() to answer a fixed way.

    No password set means everyone is an admin; a password set that no session
    token matches means nobody is.
    """

    def __init__(self, admin: bool) -> None:
        self._admin = admin

    def is_password_set(self) -> bool:
        return not self._admin

    @property
    def session_token(self) -> str:
        return "not-the-session-cookie"


def make_route_app(blueprint, linked_endpoints, admin: bool = True):
    """A Flask app with just enough wiring to render one blueprint's templates.

    `linked_endpoints` are the (rule, endpoint) pairs the templates url_for()
    but the blueprint under test does not itself define, so they need stubs.
    `admin` decides what the authorization gate makes of the caller.
    """
    app = Flask(__name__, template_folder=str(PIKARAOKE_PACKAGE / "templates"))
    app.secret_key = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin)
    Babel(app)
    app.register_blueprint(blueprint)
    endpoints = {endpoint: rule for rule, endpoint in _BASE_TEMPLATE_ENDPOINTS}
    endpoints.update({endpoint: rule for rule, endpoint in linked_endpoints})
    for endpoint, rule in endpoints.items():
        # The blueprint under test defines some of these for real; stubbing over
        # one would replace the view the test is exercising.
        if endpoint not in app.view_functions:
            app.add_url_rule(rule, endpoint, public(lambda: ""), methods=["GET"])

    @app.context_processor
    def inject_path_config():
        return {"base_path": "", "socketio_path": "/socket.io", "cookie_path": "/"}

    # app.py binds these at import for the same reason: base.html calls them on
    # every render, so a page rendered without them fails on an undefined name.
    # The session pair is off unless a test overrides it.
    app.jinja_env.globals.update(
        url_escape=quote,
        is_admin=lambda: admin,
        has_active_session=lambda: False,
        active_session_name=lambda: "",
    )
    install_auth_gate(app)
    return app


@pytest.fixture
def client(app):
    """Test client for whichever `app` fixture the test module defines."""
    return app.test_client()


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

    def __init__(self) -> None:
        # End reasons passed to skip(), in order. Held on the instance so one
        # test cannot see another's calls.
        self.skipped_reasons: list[str] = []

    # Mirrors the real signature: transpose skips with its own reason so play
    # history can tell a restart from a real skip, and tests assert on it.
    def skip(self, log_action: bool = True, reason: str = "skip") -> bool:
        self.skipped_reasons.append(reason)
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

    def display_name_from_path(self, file_path, remove_youtube_id=True):
        return SongManager.filename_from_path(file_path, remove_youtube_id)

    def rename(self, song_path, new_name):
        """Swap the path in the song list; the real one also touches disk and DB."""
        directory = song_path[: len(song_path) - len(PurePath(song_path).name)]
        new_path = directory + new_name + PurePath(song_path).suffix
        self.songs.remove(song_path)
        self.songs.add(new_path)
        return new_path


class MockSoundManager:
    """Minimal mock of SoundManager for testing."""

    def stop(self):
        pass


class MockPlayHistory:
    """Minimal mock of PlayHistoryManager, which needs a database in the real thing."""

    def __init__(self, session=None, turns_taken=None):
        self.session = session
        # {lower-case name: songs sung tonight}, for staging a session already
        # under way.
        self.turns_taken = turns_taken or {}

    def get_current_session(self) -> dict | None:
        return self.session

    def get_turns_taken(self) -> dict[str, int]:
        return self.turns_taken

    def get_current_session_name(self) -> str | None:
        return self.session["name"] if self.session else None

    def has_active_session(self) -> bool:
        return self.session is not None


class MockKeepAwake:
    """Minimal mock of KeepAwake that records whether the lock is held."""

    def __init__(self):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class MockKaraoke:
    """Minimal mock of the Karaoke class for testing queue operations.

    This mock isolates the queue logic from external dependencies like
    filesystem, network, subprocess (ffmpeg, yt-dlp), etc.
    """

    def __init__(self, tmp_path):
        self.song_manager = MockSongManager()
        self.sound_manager = MockSoundManager()
        self._keep_awake = MockKeepAwake()
        self._socketio = None
        self.events = EventSystem()
        self.preferences = PreferenceManager(
            config_file_path=str(tmp_path / "config.ini"), target=self
        )
        self.playback_controller = MockPlaybackController()
        self._playback_lock = threading.Lock()
        self.play_history = MockPlayHistory()
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
            get_turns_taken=lambda: self.play_history.get_turns_taken(),
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
    keep_awake = Karaoke.keep_awake
    get_now_playing = Karaoke.get_now_playing
    is_song_in_use = Karaoke.is_song_in_use
    rename_song = Karaoke.rename_song
    reset_now_playing = Karaoke.reset_now_playing
    transpose_current = Karaoke.transpose_current
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
