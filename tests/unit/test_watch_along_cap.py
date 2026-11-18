"""The watch-along cap: a phone gets a slot only while the room is under it.

A watching phone pulls a full duplicate of the video bitrate, so on a shared
Wi-Fi link the count is capped by an admin preference. The registry is socket
state, freed by unregister_watcher or by the socket closing.
"""

from unittest.mock import MagicMock

import pytest
from flask import Flask
from flask_socketio import SocketIO

from pikaraoke.routes import socket_events
from pikaraoke.routes.socket_events import setup_socket_events


@pytest.fixture(autouse=True)
def reset_watch_state():
    yield
    socket_events.watch_connections.clear()


def make_app(cap: int):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    karaoke = MagicMock()
    karaoke.max_phone_watchers = cap
    app.config["KARAOKE_INSTANCE"] = karaoke
    socketio = SocketIO(app, async_mode="threading")
    setup_socket_events(socketio)
    return app, socketio


def _reply(client):
    names = [m["name"] for m in client.get_received()]
    return next((n for n in names if n in ("watch_granted", "watch_denied")), None)


def test_a_phone_under_the_cap_is_granted():
    app, socketio = make_app(cap=3)
    client = socketio.test_client(app)

    client.emit("register_watcher")

    assert _reply(client) == "watch_granted"


def test_the_phone_past_the_cap_is_denied():
    app, socketio = make_app(cap=2)
    full = [socketio.test_client(app) for _ in range(2)]
    for c in full:
        c.emit("register_watcher")

    latecomer = socketio.test_client(app)
    latecomer.emit("register_watcher")

    assert _reply(latecomer) == "watch_denied"
    assert len(socket_events.watch_connections) == 2


def test_unregister_frees_a_slot_for_the_next_phone():
    app, socketio = make_app(cap=1)
    holder = socketio.test_client(app)
    holder.emit("register_watcher")
    assert _reply(holder) == "watch_granted"

    waiter = socketio.test_client(app)
    waiter.emit("register_watcher")
    assert _reply(waiter) == "watch_denied"

    holder.emit("unregister_watcher")
    waiter.emit("register_watcher")

    assert _reply(waiter) == "watch_granted"


def test_a_disconnect_frees_the_slot():
    app, socketio = make_app(cap=1)
    holder = socketio.test_client(app)
    holder.emit("register_watcher")
    assert _reply(holder) == "watch_granted"

    holder.disconnect()
    waiter = socketio.test_client(app)
    waiter.emit("register_watcher")

    assert _reply(waiter) == "watch_granted"


def test_the_same_phone_re_asking_does_not_take_a_second_slot():
    app, socketio = make_app(cap=1)
    client = socketio.test_client(app)

    client.emit("register_watcher")
    client.emit("register_watcher")

    assert len(socket_events.watch_connections) == 1
