"""Socket.IO event handlers for PiKaraoke."""

import logging

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

    @socketio.on("request_mic_devices")
    def handle_request_mic_devices() -> None:
        """Client requests the current mic device list from the server."""
        k = get_karaoke_instance()
        socketio.emit("mic_devices_state", k.mic_manager.get_enriched_devices(), room=request.sid)

    @socketio.on("request_output_devices")
    def handle_request_output_devices() -> None:
        """Client requests the current audio output device list."""
        k = get_karaoke_instance()
        socketio.emit(
            "output_devices_state", k.mic_manager.get_output_devices_state(), room=request.sid
        )

    @socketio.on("output_device_change")
    def handle_output_device_change(data: dict) -> None:
        """Handle audio output device selection from control UI."""
        k = get_karaoke_instance()
        output_id = data.get("outputDevice") or None
        k.mic_manager.set_output_device(output_id)
        logging.info(f"Output device changed: {output_id or 'system default'}")
        socketio.emit("output_devices_state", k.mic_manager.get_output_devices_state())

    @socketio.on("request_mic_settings")
    def handle_request_mic_settings() -> None:
        """Client requests current mic global settings (latency, echo cancel)."""
        k = get_karaoke_instance()
        socketio.emit(
            "mic_settings_state",
            {
                "latency_ms": k.mic_manager.get_latency_ms(),
                "echo_cancel": k.mic_manager.get_echo_cancel(),
            },
            room=request.sid,
        )

    @socketio.on("mic_latency_change")
    def handle_mic_latency_change(data: dict) -> None:
        """Handle mic latency change from control UI."""
        k = get_karaoke_instance()
        latency_ms = int(data.get("latency_ms", 50))
        k.mic_manager.set_latency_ms(latency_ms)
        socketio.emit(
            "mic_settings_state",
            {
                "latency_ms": k.mic_manager.get_latency_ms(),
                "echo_cancel": k.mic_manager.get_echo_cancel(),
            },
        )

    @socketio.on("mic_echo_cancel_change")
    def handle_mic_echo_cancel_change(data: dict) -> None:
        """Handle echo cancellation toggle from control UI."""
        k = get_karaoke_instance()
        enabled = bool(data.get("enabled", False))
        k.mic_manager.set_echo_cancel(enabled)
        socketio.emit(
            "mic_settings_state",
            {
                "latency_ms": k.mic_manager.get_latency_ms(),
                "echo_cancel": k.mic_manager.get_echo_cancel(),
            },
        )

    @socketio.on("mic_refresh")
    def handle_mic_refresh() -> None:
        """Re-enumerate mic and output devices server-side and broadcast updated lists."""
        k = get_karaoke_instance()
        enriched = k.mic_manager.refresh()
        socketio.emit("mic_devices_state", enriched)
        socketio.emit("output_devices_state", k.mic_manager.get_output_devices_state())

    @socketio.on("mic_update")
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
            settings = k.mic_manager.load_settings()
            new_state = {"enabled": enabled, "volume": volume}
            if settings.get(label) == new_state:
                return
            settings[label] = new_state
            k.mic_manager.save_settings(settings)

        # Activate or deactivate the mic stream server-side
        if enabled:
            k.mic_manager.activate(device_id, volume)
        else:
            k.mic_manager.deactivate(device_id)

        logging.info(f"Mic update: {label} enabled={enabled} volume={volume}")
        socketio.emit("mic_update", data)
