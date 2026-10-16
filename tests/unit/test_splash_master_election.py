"""Master-election for the splash screens.

The master is the splash that is actually playing the current playback, not the
one that happened to register first. A kiosk that restores a pile of stale
/splash tabs otherwise hands the seat to a dead tab, which then never reports a
position or an end. These tests cover the liveness + playback-id decision logic
and the handler wiring, driving the real handlers with a stub karaoke.
"""

from unittest.mock import MagicMock

import pytest
from flask import Flask
from flask_socketio import SocketIO

from pikaraoke.routes import socket_events
from pikaraoke.routes.socket_events import (
    MASTER_LIVENESS_S,
    SPLASH_REAP_S,
    _is_alive,
    _is_master_candidate,
    _is_master_qualified,
    _reap_quiet_sids,
    setup_socket_events,
)
from tests.conftest import StubAdminAuth


@pytest.fixture(autouse=True)
def reset_election_state():
    """Election state is module-level, so it outlives the test that set it."""
    yield
    socket_events.splash_connections.clear()
    socket_events.master_splash_id = None
    socket_events.splash_liveness.clear()
    socket_events.splash_last_playback.clear()


def make_socket_app(admin: bool = True):
    """A Flask app with the socket handlers and a stub karaoke that keeps real
    position / playback-id fields (a bare MagicMock would mask "never set")."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin)
    karaoke = MagicMock()
    karaoke.playback_controller.now_playing_position = None
    karaoke.playback_controller.playback_id = None
    app.config["KARAOKE_INSTANCE"] = karaoke
    socketio = SocketIO(app, async_mode="threading")
    setup_socket_events(socketio)
    return app, socketio, karaoke


def register(socketio, app):
    """Register one splash client and return (client, its server sid)."""
    before = set(socket_events.splash_connections)
    client = socketio.test_client(app)
    client.emit("register_splash")
    added = set(socket_events.splash_connections) - before
    assert len(added) == 1
    return client, added.pop()


class TestLivenessHelpers:
    """The pure decision functions behind the election."""

    def test_is_alive_within_window(self):
        socket_events.splash_liveness["a"] = 100.0
        assert _is_alive("a", 100.0 + MASTER_LIVENESS_S) is True
        assert _is_alive("a", 100.0 + MASTER_LIVENESS_S + 0.1) is False

    def test_is_alive_none_sid(self):
        assert _is_alive(None, 100.0) is False

    def test_is_alive_unknown_sid(self):
        assert _is_alive("ghost", 100.0) is False

    def test_candidate_qualifies_when_nothing_loaded(self):
        # No playback loaded: any beacon may hold the seat.
        assert _is_master_candidate("whatever", None) is True
        assert _is_master_candidate(None, None) is True

    def test_candidate_requires_the_loaded_playback(self):
        assert _is_master_candidate("PB2", "PB2") is True
        # A beacon for another (stale) playback is not a candidate.
        assert _is_master_candidate("PB1", "PB2") is False
        # A beacon that does not name the loaded playback (older clients omit the
        # id) cannot hold the seat for a loaded song.
        assert _is_master_candidate(None, "PB2") is False

    def test_master_qualified_needs_alive_and_current_playback(self):
        now = 100.0
        socket_events.splash_liveness["m"] = now
        # Alive, no playback loaded -> qualified.
        assert _is_master_qualified("m", now, None) is True
        # Alive, last beacon on the loaded playback -> qualified.
        socket_events.splash_last_playback["m"] = "PB2"
        assert _is_master_qualified("m", now, "PB2") is True
        # Alive but stuck on an ended playback -> not qualified.
        socket_events.splash_last_playback["m"] = "PB1"
        assert _is_master_qualified("m", now, "PB2") is False
        # Not alive at all -> not qualified.
        socket_events.splash_liveness["m"] = now - MASTER_LIVENESS_S - 1
        assert _is_master_qualified("m", now, "PB2") is False

    def test_master_qualified_none_sid(self):
        assert _is_master_qualified(None, 100.0, "PB2") is False


class TestElectionByBeacon:
    """A dead first-registered tab cannot hold the seat; a live tab does."""

    def test_dead_first_registrant_does_not_take_seat(self):
        """A (dead) registers first, B (live) beacons: B wins, not A."""
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)
        b, b_sid = register(socketio, app)

        # A is dead: it registered but never beacons. B is playing.
        karaoke.playback_controller.playback_id = None
        b.emit("playback_position", 42.0, None)

        assert socket_events.master_splash_id == b_sid
        assert socket_events.master_splash_id != a_sid
        assert karaoke.playback_controller.now_playing_position == 42.0

    def test_first_live_tab_becomes_master_and_is_heard(self):
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)

        a.emit("playback_position", 10.0, None)

        assert socket_events.master_splash_id == a_sid
        assert karaoke.playback_controller.now_playing_position == 10.0

    def test_stable_master_not_displaced_by_another_live_tab(self):
        """Two live tabs: the incumbent keeps the seat (no flapping)."""
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 10.0, None)
        assert socket_events.master_splash_id == a_sid

        b, b_sid = register(socketio, app)
        b.emit("playback_position", 11.0, None)

        # A is alive and on the current (unspecified) playback, so it stays.
        assert socket_events.master_splash_id == a_sid
        # B's position is not accepted: B is not the master.
        assert karaoke.playback_controller.now_playing_position == 10.0

    def test_stale_playback_master_is_replaced(self):
        """A master stuck on an ended playback loses the seat to a live tab on
        the new playback."""
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 5.0, "PB1")
        assert socket_events.master_splash_id == a_sid

        # The song turns over: a new playback is loaded.
        karaoke.playback_controller.playback_id = "PB2"
        b, b_sid = register(socketio, app)
        b.emit("playback_position", 1.0, "PB2")

        assert socket_events.master_splash_id == b_sid
        assert karaoke.playback_controller.now_playing_position == 1.0

    def test_stalled_master_is_replaced_after_liveness_window(self):
        """A master that goes quiet past the liveness window loses the seat."""
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 10.0, None)
        assert socket_events.master_splash_id == a_sid

        # A stops beaming: age its last beat past the window.
        socket_events.splash_liveness[a_sid] = (
            socket_events.splash_liveness[a_sid] - MASTER_LIVENESS_S - 1
        )

        b, b_sid = register(socketio, app)
        b.emit("playback_position", 20.0, None)

        assert socket_events.master_splash_id == b_sid
        assert karaoke.playback_controller.now_playing_position == 20.0

    def test_disconnected_master_seat_is_released(self):
        """When the master disconnects, the seat clears and a live tab retakes it."""
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 10.0, None)
        assert socket_events.master_splash_id == a_sid

        # Disconnect the master (drives the disconnect handler for a_sid).
        a.disconnect()
        assert socket_events.master_splash_id is None
        assert a_sid not in socket_events.splash_connections
        assert a_sid not in socket_events.splash_liveness
        assert a_sid not in socket_events.splash_last_playback

        # A live tab retakes the seat.
        b, b_sid = register(socketio, app)
        b.emit("playback_position", 30.0, None)
        assert socket_events.master_splash_id == b_sid

    def test_non_master_beacon_does_not_set_position(self):
        """A slave's beacon proves it alive but does not move the position."""
        app, socketio, karaoke = make_socket_app()
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 10.0, None)
        assert socket_events.master_splash_id == a_sid

        b, b_sid = register(socketio, app)
        b.emit("playback_position", 999.0, None)

        # B is alive now, but A stays master and the position is A's.
        assert socket_events.master_splash_id == a_sid
        assert karaoke.playback_controller.now_playing_position == 10.0
        assert socket_events.splash_liveness.get(b_sid) is not None

    def test_stale_playback_beacon_does_not_set_position(self):
        """A beacon for a playback other than the loaded one is ignored."""
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = "PB2"
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 10.0, "PB2")
        assert karaoke.playback_controller.now_playing_position == 10.0

        # A late beacon for the previous playback must not move the position.
        a.emit("playback_position", 999.0, "PB1")
        assert karaoke.playback_controller.now_playing_position == 10.0


class TestRegistrationGate:
    """The seat and the position are only ever set by a registered splash."""

    def test_unregistered_beacon_cannot_grab_the_seat(self):
        """A bare beacon from a client that never registered must not take the
        master seat or set the position."""
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = None
        karaoke.playback_controller.now_playing_position = None

        rogue = socketio.test_client(app)
        rogue.emit("playback_position", 42.0, None)

        assert socket_events.master_splash_id is None
        assert karaoke.playback_controller.now_playing_position is None
        # Nothing was recorded for an unregistered client either.
        assert not socket_events.splash_liveness

    def test_unregistered_beacon_cannot_displace_a_healthy_master(self):
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = "PB1"
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 10.0, "PB1")
        assert socket_events.master_splash_id == a_sid

        rogue = socketio.test_client(app)
        rogue.emit("playback_position", 999.0, "PB1")

        assert socket_events.master_splash_id == a_sid
        assert karaoke.playback_controller.now_playing_position == 10.0

    def test_a_reaped_tab_stays_trusted(self):
        """A tab reaped of its bookkeeping must still pass the registration gate
        on its next beacon. Reaping only touches the liveness/playback dicts,
        never the trust set (splash_connections), so a tab that was merely
        paused - not a stale one - is not locked out of the seat."""
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = None
        b, _ = register(socketio, app)
        b.emit("playback_position", 20.0, None)  # b takes the master seat
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 11.0, None)  # a is a live slave

        # Age a well past the reap window; it pauses and stops beaming.
        socket_events.splash_liveness[a_sid] -= SPLASH_REAP_S + 1
        b.emit("playback_position", 21.0, None)  # a beacon triggers the reap

        # a (a non-master, quiet) was reaped of its bookkeeping...
        assert a_sid not in socket_events.splash_liveness
        # ...but it is still a registered splash, so its next beacon is trusted.
        a.emit("playback_position", 12.0, None)
        assert a_sid in socket_events.splash_liveness


class TestEndSongMasterGate:
    """Only the master splash may end the current song over the public /splash
    namespace; a guest's console emit is dropped. (The master's own end and the
    'failed to start' rule are covered in test_socket_mic_gate.)"""

    def test_non_master_end_song_is_ignored(self):
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = "PB1"
        master, _ = register(socketio, app)
        master.emit("playback_position", 10.0, "PB1")

        guest, _ = register(socketio, app)
        guest.emit("end_song", "complete", "PB1")

        karaoke.playback_controller.end_song.assert_not_called()

    def test_no_master_means_no_client_end(self):
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = "PB1"
        a, _ = register(socketio, app)  # registered but never beacons

        a.emit("end_song", "complete", "PB1")

        assert socket_events.master_splash_id is None
        karaoke.playback_controller.end_song.assert_not_called()

    def test_demoted_master_can_no_longer_end(self):
        app, socketio, karaoke = make_socket_app()
        karaoke.playback_controller.playback_id = "PB1"
        a, a_sid = register(socketio, app)
        a.emit("playback_position", 5.0, "PB1")
        assert socket_events.master_splash_id == a_sid

        karaoke.playback_controller.playback_id = "PB2"
        b, b_sid = register(socketio, app)
        b.emit("playback_position", 1.0, "PB2")
        assert socket_events.master_splash_id == b_sid

        a.emit("end_song", "complete", "PB2")
        karaoke.playback_controller.end_song.assert_not_called()

        b.emit("end_song", "complete", "PB2")
        karaoke.playback_controller.end_song.assert_called_once_with("complete", "PB2")


class TestQuietSidsAreReaped:
    """Bookkeeping for sids that stop beaming is dropped, so a churn of restored
    tabs that never deliver a clean disconnect cannot grow the state without
    bound."""

    def test_quiet_non_master_sid_is_reaped_on_the_next_beacon(self):
        socket_events.splash_connections.add("a")
        socket_events.splash_connections.add("b")
        socket_events.splash_liveness["a"] = 100.0
        socket_events.splash_liveness["b"] = 100.0
        socket_events.splash_last_playback["a"] = "PB1"
        socket_events.splash_last_playback["b"] = None
        socket_events.master_splash_id = "b"

        # B is the master; A has been quiet well past the window.
        _reap_quiet_sids(100.0 + SPLASH_REAP_S + 1)

        assert "a" not in socket_events.splash_liveness
        assert "a" not in socket_events.splash_last_playback
        # The master and its bookkeeping are always kept.
        assert socket_events.splash_liveness["b"] == 100.0
        assert socket_events.splash_last_playback["b"] is None
        # The trust set is managed by register/disconnect, never by the reap.
        assert socket_events.splash_connections == {"a", "b"}

    def test_recent_sid_is_not_reaped(self):
        socket_events.splash_connections.add("a")
        socket_events.splash_liveness["a"] = 100.0
        socket_events.splash_last_playback["a"] = "PB1"
        socket_events.master_splash_id = None

        _reap_quiet_sids(100.0 + SPLASH_REAP_S)

        assert "a" in socket_events.splash_liveness
        assert socket_events.splash_last_playback["a"] == "PB1"
