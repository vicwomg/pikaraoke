"""Flask application context utilities for PiKaraoke."""

import logging
import os
import subprocess
import sys
import time
from typing import Any

from flask import current_app, request, session
from flask_socketio import emit

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.admin_auth import AdminAuth


def is_admin() -> bool:
    """Whether this request is authenticated as the admin.

    With no admin password set everyone is an admin -- the right default for a box
    on a home TV. Otherwise it takes a signed session established by /auth.
    """
    auth = get_admin_auth()
    return not auth.is_password_set() or session.get("admin") == auth.session_token


def get_karaoke_instance() -> Karaoke:
    """Get the current app's Karaoke instance
    This function returns the Karaoke instance stored in the current app's configuration.
    Returns:
        Karaoke: The Karaoke instance stored in the current app's configuration.
    """
    return current_app.config["KARAOKE_INSTANCE"]


def get_admin_auth() -> AdminAuth:
    """Get the current app's admin authentication store."""
    return current_app.config["ADMIN_AUTH"]


def get_site_name() -> str:
    """Get the site name from the current app's configuration
    This function returns the site name stored in the current app's configuration.
    Returns:
        str: The site name stored in the current app's configuration.
    """
    return current_app.config["SITE_NAME"]


def broadcast_event(event: str, data: Any = None) -> None:
    """Broadcast a SocketIO event to all connected clients.

    Args:
        event: Name of the event to broadcast.
        data: Optional data payload to send with the event.
    """
    logging.debug("Broadcasting event: " + event)
    emit(event, data, namespace="/", broadcast=True)


def delayed_halt(cmd: int) -> None:
    """Execute a delayed system halt command.

    Clears the queue, stops the karaoke instance, then executes the command.

    Args:
        cmd: Command to execute:
            0 = exit application
            1 = shutdown system
            2 = reboot system
            3 = expand rootfs and reboot (Raspberry Pi)
    """
    time.sleep(1.5)
    k = get_karaoke_instance()
    k.queue_manager.queue_clear()
    k.stop()
    if cmd == 0:
        sys.exit()
    if cmd == 1:
        os.system("shutdown now")
    if cmd == 2:
        os.system("reboot")
    if cmd == 3:
        process = subprocess.Popen(["raspi-config", "--expand-rootfs"])
        process.wait()
        os.system("reboot")
