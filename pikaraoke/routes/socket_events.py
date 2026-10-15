"""Socket.IO event handlers for PiKaraoke."""

import logging
import time
from collections.abc import Callable
from functools import wraps

from flask import request

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin

# Track connected splash screen clients, the elected master, when each client
# last proved it was alive, and which playback each last reported.
splash_connections: set[str] = set()
master_splash_id: str | None = None
splash_liveness: dict[str, float] = {}
splash_last_playback: dict[str, str | None] = {}

# How long a splash may go quiet before it is no longer counted alive for master
# election. The live kiosk tab beacons once a second while it plays, so a few
# missed beacons (a greenlet stall, a heavy transcode) must not dislodge a
# healthy master, while a dead or restored tab that stops beaming is dropped.
MASTER_LIVENESS_S = 3.0

# How long a splash may stay quiet before its per-beacon bookkeeping (liveness,
# last playback) is reaped, so a churn of restored tabs that never deliver a
# clean disconnect cannot grow those dicts without bound. A registered tab that
# is merely paused stays in the trust set (splash_connections) and simply reads
# as not-alive until it beacons again.
SPLASH_REAP_S = MASTER_LIVENESS_S * 4


def _is_alive(sid: str | None, now: float) -> bool:
    """Whether a splash beat within the liveness window."""
    if sid is None:
        return False
    last = splash_liveness.get(sid)
    return last is not None and (now - last) <= MASTER_LIVENESS_S


def _is_master_candidate(playback_id: str | None, current_playback_id: str | None) -> bool:
    """Whether a beacon is from a splash that may hold the master seat.

    The master is a splash playing the playback the server has loaded. When the
    server has none loaded (nothing playing) any beacon qualifies; once a
    playback is loaded, the beacon must name it. A beacon that does not name a
    playback (older clients omit the id) cannot prove it is playing the loaded
    one, so it cannot hold the seat for a loaded song - a restored tab stuck on
    a previous song stays out.
    """
    if current_playback_id is None:
        return True
    return playback_id == current_playback_id


def _is_master_qualified(sid: str | None, now: float, current_playback_id: str | None) -> bool:
    """Whether the current master is still fit to hold the seat.

    It must be alive and, once the server knows which playback is loaded, it
    must be the one the master last reported - not a stale, already-ended
    playback. This is what lets a live tab take the seat from one that is alive
    but stuck on a previous song.
    """
    if sid is None or not _is_alive(sid, now):
        return False
    if current_playback_id is None:
        return True
    beacon = splash_last_playback.get(sid)
    return beacon is None or beacon == current_playback_id


def _reap_quiet_sids(now: float) -> None:
    """Drop the bookkeeping of sids that have been quiet well past the window.

    Called on each beacon (the liveness signal itself), so the liveness and
    last-playback dicts stay bounded even when tabs churn without a clean
    disconnect. The trust set (splash_connections) is never pruned here - it is
    the registration record, managed by register/disconnect, so a registered
    tab that pauses must stay eligible to re-take the seat.
    """
    for sid in list(splash_liveness):
        if sid == master_splash_id:
            continue
        if now - splash_liveness[sid] > SPLASH_REAP_S:
            splash_liveness.pop(sid, None)
            splash_last_playback.pop(sid, None)


def _guard(handler: Callable) -> Callable:
    """Drop an event from a client with no admin session.

    Refusing costs no reply: the only control that sends a host-only event is
    itself host-only in the template.
    """

    @wraps(handler)
    def guarded(*args, **kwargs):
        if not is_admin():
            logging.warning(f"Refused {handler.__name__}: no admin session")
            return None
        return handler(*args, **kwargs)

    return guarded


def setup_socket_events(socketio):
    """Register Socket.IO event handlers.

    Args:
        socketio: The SocketIO instance.
    """

    # The route gate is a before_request and never sees this surface, so each
    # handler names its own audience instead. Registering through neither of
    # these is the one way to open an event by accident, and the tests fail on
    # a handler that appears in neither roster.
    open_to_room = socketio.on

    def host_only(event: str) -> Callable:
        """Register a handler a guest's emit must not reach."""

        def register(handler: Callable) -> Callable:
            socketio.on(event)(_guard(handler))
            return handler

        return register

    @open_to_room("end_song")
    def end_song(reason: str, playback_id: str | None = None) -> None:
        """Handle end_song WebSocket event from client.

        Args:
            reason: Reason for ending the song (e.g., 'complete', 'error').
            playback_id: Playback the player is reporting on.
        """
        k = get_karaoke_instance()
        k.playback_controller.end_song(reason, playback_id)

    @open_to_room("start_song")
    def start_song(playback_id: str | None = None) -> None:
        """Handle start_song WebSocket event when playback begins.

        Args:
            playback_id: Playback the player is reporting on.
        """
        k = get_karaoke_instance()
        k.playback_controller.start_song(playback_id)

    @open_to_room("clear_notification")
    def clear_notification() -> None:
        """Handle clear_notification WebSocket event to dismiss notifications."""
        k = get_karaoke_instance()
        k.reset_now_playing_notification()

    @open_to_room("register_splash")
    def register_splash() -> None:
        """Handle splash screen registration and note the client as alive.

        Registration alone is not what makes a client the master: it only marks
        the client alive. The seat is held by a client that proves it is playing
        the current playback, via its position beacon (playback_position), so a
        dead or restored tab that merely registers cannot claim it.
        """
        sid = request.sid
        splash_connections.add(sid)
        splash_liveness[sid] = time.monotonic()
        logging.info(f"Splash screen registered: {sid}")

    @open_to_room("playback_position")
    def handle_playback_position(position: float, playback_id: str | None = None) -> None:
        """Handle a splash beacon: a position report that also proves the sender
        is alive.

        The client only emits this while its video is playing, so each beacon
        proves the sender is a live, playing splash. The sender is marked alive.
        If it is a valid master candidate (playing the playback the server has
        loaded) and the current master is not alive, it takes the seat. The
        position is accepted only from the master, and a beacon for a playback
        other than the one loaded is ignored so a late report for an ended song
        cannot move the position of its successor.
        """
        global master_splash_id
        sid = request.sid
        # Only a registered splash may prove liveness, take the seat, or set the
        # authoritative position. A bare beacon from an unregistered client
        # (another page, a stale or malicious client) must not be able to grab
        # the master seat.
        if sid not in splash_connections:
            return
        now = time.monotonic()
        splash_liveness[sid] = now
        splash_last_playback[sid] = playback_id
        _reap_quiet_sids(now)

        k = get_karaoke_instance()
        current_playback_id = k.playback_controller.playback_id

        if _is_master_candidate(playback_id, current_playback_id):
            # This splash is playing the current playback. If it is not already
            # master, and the current master is no longer fit to hold the seat
            # (disconnected, gone quiet, or stuck on an ended playback), this
            # one takes the seat.
            if sid != master_splash_id and not _is_master_qualified(
                master_splash_id, now, current_playback_id
            ):
                displaced = master_splash_id
                master_splash_id = sid
                # Tell the displaced master it no longer holds the seat, so a
                # still-connected tab that thought it was the master (and would
                # otherwise emit end reports) goes back to read-only.
                if displaced is not None and displaced in splash_connections:
                    socketio.emit("splash_role", "slave", room=displaced)
                socketio.emit("splash_role", "master", room=sid)
                logging.info(
                    f"Master splash elected by live beacon: {sid} " f"(displaced {displaced})"
                )

        # Only the master's position is authoritative, and only for the playback
        # it is playing.
        if sid == master_splash_id:
            if (
                playback_id is not None
                and current_playback_id is not None
                and (playback_id != current_playback_id)
            ):
                logging.debug(
                    f"Ignoring position for playback {playback_id} "
                    f"(current {current_playback_id})"
                )
                return
            k.playback_controller.now_playing_position = position
            socketio.emit("playback_position", position, include_self=False)

    @open_to_room("disconnect")
    def handle_disconnect() -> None:
        """Handle Socket.IO client disconnection and clear the client's liveness."""
        global master_splash_id
        sid = request.sid
        if sid in splash_connections:
            splash_connections.remove(sid)
            splash_liveness.pop(sid, None)
            splash_last_playback.pop(sid, None)
            logging.info(f"Splash screen disconnected: {sid}")
        # If the master disconnected, clear the seat; the next live beacon
        # reassigns it to a splash that is actually playing the current song.
        if sid == master_splash_id:
            master_splash_id = None
            logging.info("Master splash disconnected, seat will go to a live splash")

    @host_only("request_mic_devices")
    def handle_request_mic_devices() -> None:
        """Client requests the current mic device list from the server."""
        k = get_karaoke_instance()
        socketio.emit(
            "mic_devices_state",
            k.sound_manager.get_enriched_devices(),
            room=request.sid,
        )

    @host_only("request_mic_settings")
    def handle_request_mic_settings() -> None:
        """Client requests current mic global settings (latency, echo cancel)."""
        k = get_karaoke_instance()
        socketio.emit(
            "mic_settings_state",
            k.sound_manager.get_mic_settings_state(),
            room=request.sid,
        )

    @host_only("mic_latency_change")
    def handle_mic_latency_change(data: dict) -> None:
        """Handle mic latency change from control UI."""
        k = get_karaoke_instance()
        latency_ms = int(data.get("latency_ms", 50))
        state = k.sound_manager.set_latency_ms(latency_ms)
        socketio.emit("mic_settings_state", state)

    @host_only("mic_echo_cancel_change")
    def handle_mic_echo_cancel_change(data: dict) -> None:
        """Handle echo cancellation toggle from control UI."""
        k = get_karaoke_instance()
        enabled = bool(data.get("enabled", False))
        state = k.sound_manager.set_echo_cancel(enabled)
        socketio.emit("mic_settings_state", state)

    @host_only("mic_refresh")
    def handle_mic_refresh() -> None:
        """Re-enumerate mic and output devices server-side and broadcast updated lists."""
        k = get_karaoke_instance()
        enriched = k.sound_manager.refresh()
        socketio.emit("mic_devices_state", enriched)

    @host_only("mic_update")
    def handle_mic_update(data: dict) -> None:
        """Handle mic configuration change from control UI.

        Persists settings and activates/deactivates mic server-side.
        """
        k = get_karaoke_instance()
        label = data.get("label", "")
        device_id = str(data.get("deviceId", ""))
        enabled = data.get("enabled", False)
        volume = data.get("volume", 1.0)

        if label:
            settings = k.sound_manager.load_settings()
            new_state = {"enabled": enabled, "volume": volume}
            if settings.get(label) == new_state:
                return
            settings[label] = new_state
            k.sound_manager.save_settings(settings)

        # Activate or deactivate the mic stream server-side
        if enabled:
            k.sound_manager.activate(device_id, volume)
        else:
            k.sound_manager.deactivate(device_id)

        logging.info(f"Mic update: {label} enabled={enabled} volume={volume}")
        socketio.emit("mic_update", data)
