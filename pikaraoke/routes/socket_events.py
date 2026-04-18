"""Socket.IO event handlers for PiKaraoke."""

import logging
import time

from flask import request

from pikaraoke.lib.current_app import get_karaoke_instance

# Track connected splash screen clients and the elected master
splash_connections = set()
master_splash_id = None


def setup_socket_events(socketio):
    """Register Socket.IO event handlers.

    Args:
        socketio: The SocketIO instance.
    """

    @socketio.on("end_song")
    def end_song(reason: str) -> None:
        """Handle end_song WebSocket event from client.

        Args:
            reason: Reason for ending the song (e.g., 'complete', 'error').
        """
        k = get_karaoke_instance()
        k.playback_controller.end_song(reason)

    @socketio.on("start_song")
    def start_song() -> None:
        """Handle start_song WebSocket event when playback begins."""
        k = get_karaoke_instance()
        k.playback_controller.start_song()

    @socketio.on("clear_notification")
    def clear_notification() -> None:
        """Handle clear_notification WebSocket event to dismiss notifications."""
        k = get_karaoke_instance()
        k.reset_now_playing_notification()

    @socketio.on("register_splash")
    def register_splash() -> None:
        """Handle splash screen registration and assign master/slave roles."""
        global master_splash_id
        sid = request.sid
        splash_connections.add(sid)
        logging.info(f"Splash screen registered: {sid}")

        if master_splash_id is None:
            master_splash_id = sid
            socketio.emit("splash_role", "master", room=sid)
            logging.info(f"Master splash screens assigned: {sid}")
        else:
            socketio.emit("splash_role", "slave", room=sid)
            logging.info(f"Slave splash screens assigned: {sid}")

    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @socketio.on("vocal_volume")
    def handle_vocal_volume(volume: float) -> None:
        """Live vocal stem volume update. Fired many times during slider drag.

        Persists the preference and broadcasts a lightweight stem_volume
        event to other clients (splash applies it to the Web Audio gain,
        other pilots sync their sliders). Heavy now_playing broadcast is
        skipped to avoid flooding during drag.
        """
        v = _clamp(volume)
        k = get_karaoke_instance()
        k.vocal_volume = v
        k.preferences.set("vocal_volume", v)
        socketio.emit("stem_volume", {"vocal_volume": v}, include_self=False)

    @socketio.on("instrumental_volume")
    def handle_instrumental_volume(volume: float) -> None:
        """Live instrumental stem volume update (same semantics as vocal)."""
        v = _clamp(volume)
        k = get_karaoke_instance()
        k.instrumental_volume = v
        k.preferences.set("instrumental_volume", v)
        socketio.emit("stem_volume", {"instrumental_volume": v}, include_self=False)

    @socketio.on("seek")
    def handle_seek(position: float) -> None:
        """Handle seek request from a pilot.

        Clamps to [0, duration] and broadcasts to all clients (splash applies
        it to the media elements; other pilots update their sliders).
        """
        try:
            pos = float(position)
        except (TypeError, ValueError):
            logging.warning(f"Ignoring invalid seek value: {position!r}")
            return
        k = get_karaoke_instance()
        duration = k.playback_controller.now_playing_duration
        if duration:
            pos = max(0.0, min(float(duration), pos))
        else:
            pos = max(0.0, pos)
        k.playback_controller.now_playing_position = pos
        k.playback_controller.position_updated_at = time.time()
        socketio.emit("seek", pos)

    @socketio.on("playback_position")
    def handle_playback_position(position: float) -> None:
        """Handle playback_position WebSocket event from the master splash screen.

        Args:
            position: Current playback position in seconds.
        """
        global master_splash_id
        sid = request.sid
        if sid == master_splash_id:
            k = get_karaoke_instance()
            k.playback_controller.now_playing_position = position
            k.playback_controller.position_updated_at = time.time()
            # Broadcast position to all other splash screens (slaves)
            socketio.emit("playback_position", position, include_self=False)

    @socketio.on("disconnect")
    def handle_disconnect() -> None:
        """Handle Socket.IO client disconnection and manage splash role handover."""
        global master_splash_id
        sid = request.sid
        if sid in splash_connections:
            splash_connections.remove(sid)
            logging.info(f"Splash screen disconnected: {sid}")
            if sid == master_splash_id:
                master_splash_id = None
                logging.info("Master splash disconnected, electing new master")
                if splash_connections:
                    # Elect new master from remaining connections
                    new_master = next(iter(splash_connections))
                    master_splash_id = new_master
                    socketio.emit("splash_role", "master", room=new_master)
                    logging.info(f"New master splash elected: {new_master}")
