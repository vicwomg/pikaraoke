"""Tests for splash screen master/slave election.

Only the master splash screen reports playback state to the server, so an
election that leaves no live master silently stops songs from ever starting.
"""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes import socket_events


class FakeSocketIO:
    """Captures Socket.IO handlers and the roles emitted to each socket."""

    def __init__(self):
        self.handlers = {}
        self.roles = []

    def on(self, event_name):
        def decorator(fn):
            self.handlers[event_name] = fn
            return fn

        return decorator

    def emit(self, event_name, payload=None, **kwargs):
        if event_name == "splash_role":
            self.roles.append((kwargs.get("room"), payload))

    def role_of(self, sid):
        """Latest role assigned to a socket, or None if it was never told."""
        for room, role in reversed(self.roles):
            if room == sid:
                return role
        return None


@pytest.fixture
def sio():
    """Register the socket handlers against a clean splash registry."""
    socket_events.splash_connections.clear()
    socket_events.screen_first_seen.clear()
    socket_events.master_splash_id = None
    fake = FakeSocketIO()
    socket_events.setup_socket_events(fake)
    yield fake
    socket_events.splash_connections.clear()
    socket_events.screen_first_seen.clear()
    socket_events.master_splash_id = None


def register(sio, sid, screen_id=None):
    with patch.object(socket_events, "request", MagicMock(sid=sid)):
        sio.handlers["register_splash"](screen_id)


def disconnect(sio, sid):
    with patch.object(socket_events, "request", MagicMock(sid=sid)):
        sio.handlers["disconnect"]()


class TestSplashMasterElection:
    def test_first_splash_is_master_and_second_is_slave(self, sio):
        register(sio, "sid-a", "screen-1")
        register(sio, "sid-b", "screen-2")

        assert sio.role_of("sid-a") == "master"
        assert sio.role_of("sid-b") == "slave"
        assert socket_events.master_splash_id == "sid-a"

    def test_reloading_master_reclaims_over_a_screen_that_stayed_up(self, sio):
        """The screen seen first holds the role across a reload. A survivor drives
        playback during the gap, then hands it back when the first screen returns,
        so the role lands where the party is watching rather than wandering."""
        register(sio, "sid-a", "screen-1")
        register(sio, "sid-b", "screen-2")

        # screen-1 reloads: its old socket drops, then a new one registers.
        disconnect(sio, "sid-a")
        assert socket_events.master_splash_id == "sid-b"
        register(sio, "sid-a2", "screen-1")

        assert socket_events.master_splash_id == "sid-a2"
        assert sio.role_of("sid-a2") == "master"
        assert sio.role_of("sid-b") == "slave"

    def test_reloading_master_reclaims_even_before_its_old_socket_drops(self, sio):
        """A slow reconnect registers while the ghost still lingers: the first screen
        still reclaims, and the survivor is never promoted in between."""
        register(sio, "sid-a", "screen-1")
        register(sio, "sid-b", "screen-2")

        register(sio, "sid-a2", "screen-1")

        assert socket_events.master_splash_id == "sid-a2"
        assert sio.role_of("sid-a2") == "master"
        assert sio.role_of("sid-b") == "slave"
        assert "sid-a" not in socket_events.splash_connections

    def test_reloading_slave_does_not_disturb_the_master(self, sio):
        register(sio, "sid-a", "screen-1")
        register(sio, "sid-b", "screen-2")

        register(sio, "sid-b2", "screen-2")

        assert socket_events.master_splash_id == "sid-a"
        assert sio.role_of("sid-b2") == "slave"

    def test_lone_master_takes_the_role_back_on_reload(self, sio):
        """With no other screen to hand off to, waiting for the ping timeout would
        leave nobody reporting playback state."""
        register(sio, "sid-a", "screen-1")

        register(sio, "sid-a2", "screen-1")

        assert socket_events.master_splash_id == "sid-a2"
        assert sio.role_of("sid-a2") == "master"
        assert socket_events.splash_connections == {"sid-a2": "screen-1"}

    def test_stale_socket_is_demoted_and_forgotten(self, sio):
        """Covers a duplicated tab, where the old socket is still alive."""
        register(sio, "sid-a", "screen-1")

        register(sio, "sid-a2", "screen-1")

        assert sio.role_of("sid-a") == "slave"
        assert "sid-a" not in socket_events.splash_connections

    def test_re_registering_does_not_demote_the_master(self, sio):
        """A socket may send register_splash more than once over its life. Re-registering
        the screen that is already master must leave it in place, or nobody is left
        reporting playback."""
        register(sio, "sid-a", "screen-1")

        for _ in range(3):
            register(sio, "sid-a", "screen-1")

        assert socket_events.master_splash_id == "sid-a"
        assert sio.roles == [("sid-a", "master")]

    def test_master_disconnect_promotes_a_survivor(self, sio):
        register(sio, "sid-a", "screen-1")
        register(sio, "sid-b", "screen-2")

        disconnect(sio, "sid-a")

        assert sio.role_of("sid-b") == "master"
        assert socket_events.master_splash_id == "sid-b"

    def test_last_splash_leaving_clears_the_master(self, sio):
        register(sio, "sid-a", "screen-1")

        disconnect(sio, "sid-a")

        assert socket_events.master_splash_id is None
        assert socket_events.splash_connections == {}

    def test_slave_disconnect_leaves_the_master_alone(self, sio):
        register(sio, "sid-a", "screen-1")
        register(sio, "sid-b", "screen-2")

        disconnect(sio, "sid-b")

        assert socket_events.master_splash_id == "sid-a"

    def test_screen_id_falls_back_to_the_socket_id(self, sio):
        """Clients that send no screen id still get exactly one master."""
        register(sio, "sid-a")
        register(sio, "sid-b")

        assert sio.role_of("sid-a") == "master"
        assert sio.role_of("sid-b") == "slave"
