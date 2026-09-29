"""Every socket handler names its audience at the point it is registered.

Their own file because the route gate is a before_request: it never sees the
socket surface, so no amount of growing its allowlist would cover these.

The two rosters below are the whole check. Each is also the parameter list for
a behavioural test, so a handler cannot be added to one just to quiet a
failure -- it gets tested as whatever it was declared to be.
"""

from unittest.mock import MagicMock

import pytest
from flask import Flask
from flask_socketio import SocketIO

from pikaraoke.routes import socket_events
from pikaraoke.routes.socket_events import setup_socket_events
from tests.conftest import StubAdminAuth

# Host-only handlers, with the arguments each takes.
ADMIN_EVENTS = [
    ("request_mic_devices", ()),
    ("request_mic_settings", ()),
    ("mic_latency_change", ({"latency_ms": 80},)),
    ("mic_echo_cancel_change", ({"enabled": True},)),
    ("mic_refresh", ()),
    ("mic_update", ({"label": "USB Mic", "deviceId": "1", "enabled": True, "volume": 0.5},)),
]

# The playback path, which splash screens drive with no admin session.
PUBLIC_EVENTS = [
    ("end_song", ("complete",)),
    ("start_song", ()),
    ("clear_notification", ()),
    ("register_splash", ()),
    ("playback_position", (12.5,)),
    ("disconnect", ()),
]


@pytest.fixture(autouse=True)
def reset_splash_state():
    """The elected master is module state, so it outlives the test that set it."""
    yield
    socket_events.splash_connections.clear()
    socket_events.master_splash_id = None


def make_socket_app(admin: bool):
    """A Flask app with the socket handlers registered and a stubbed karaoke."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin)

    # Real return values, not bare MagicMocks: the handlers emit what they get
    # back, and socketio serialises it to JSON.
    sound_manager = MagicMock()
    sound_manager.get_enriched_devices.return_value = []
    sound_manager.get_mic_settings_state.return_value = {}
    sound_manager.set_latency_ms.return_value = {}
    sound_manager.set_echo_cancel.return_value = {}
    sound_manager.refresh.return_value = []
    sound_manager.load_settings.return_value = {}

    karaoke = MagicMock()
    karaoke.sound_manager = sound_manager
    app.config["KARAOKE_INSTANCE"] = karaoke

    socketio = SocketIO(app, async_mode="threading")
    setup_socket_events(socketio)
    return app, socketio, karaoke


def test_every_handler_declares_its_audience():
    """A handler in neither roster was registered without the question asked."""
    _, socketio, _ = make_socket_app(admin=True)
    declared = {event for event, _ in ADMIN_EVENTS} | {event for event, _ in PUBLIC_EVENTS}

    undeclared = set(socketio.server.handlers["/"]) - declared

    assert not undeclared, (
        f"{sorted(undeclared)} is registered but declared nowhere: add it to "
        "ADMIN_EVENTS if it needs the host, PUBLIC_EVENTS if the room may send it"
    )


@pytest.mark.parametrize("event,args", ADMIN_EVENTS)
def test_admin_handler_ignores_a_guest(event, args):
    """With a password set, a guest's emit never reaches the sound manager."""
    app, socketio, karaoke = make_socket_app(admin=False)
    client = socketio.test_client(app)

    client.emit(event, *args)

    assert karaoke.sound_manager.method_calls == []


@pytest.mark.parametrize("event,args", ADMIN_EVENTS)
def test_admin_handler_serves_the_host(event, args):
    """With no password set everyone is the host, so the controls still work."""
    app, socketio, karaoke = make_socket_app(admin=True)
    client = socketio.test_client(app)

    client.emit(event, *args)

    assert karaoke.sound_manager.method_calls, f"{event} reached nothing"


@pytest.mark.parametrize("event,args", PUBLIC_EVENTS)
def test_public_handler_is_not_refused(event, args, caplog):
    """Registering one of these as host-only would lock every splash screen out.

    The guard's refusal is a log line, so absence of one is what open means
    here -- what each handler then does is its own test's business.
    """
    app, socketio, _ = make_socket_app(admin=False)
    client = socketio.test_client(app)

    client.emit(event, *args)

    assert "Refused" not in caplog.text, f"{event} is gated rather than open"


def test_a_guest_splash_registers_and_reports_position():
    """Election and position in one test: only the elected master is heard."""
    app, socketio, karaoke = make_socket_app(admin=False)
    client = socketio.test_client(app)

    client.emit("register_splash")
    client.emit("playback_position", 12.5)

    assert any(m["name"] == "splash_role" for m in client.get_received())
    assert karaoke.playback_controller.now_playing_position == 12.5
