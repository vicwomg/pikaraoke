"""Flask application context utilities for PiKaraoke."""

import logging
from typing import Any
from urllib.parse import unquote

from flask import current_app, g, request, session
from flask_socketio import emit

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.login_throttle import LoginThrottle


def current_singer_name() -> str | None:
    """The singer named by this device's cookie, or None when unnamed.

    js-cookie percent-encodes what it writes, so the stored value is decoded
    here; a blank or whitespace-only name counts as none. This is the server's
    one read of the identity cookie -- favourite stars are drawn from it.
    """
    raw = request.cookies.get("user")
    if not raw:
        return None
    return unquote(raw).strip() or None


def favourite_paths() -> set[str]:
    """This device's singer's starred songs that are in the library, cached per request.

    A template global: the song lists call it per row to draw their stars, so it
    must cost one query per render however many rows ask. Empty when the device
    has no name, so an unnamed browser draws every star unfilled rather than
    hidden; clicking one then asks for a name.
    """
    if "favourite_paths" not in g:
        name = current_singer_name()
        g.favourite_paths = (
            get_karaoke_instance().singer_manager.get_favourite_paths(name) if name else set()
        )
    return g.favourite_paths


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


def get_login_throttle() -> LoginThrottle:
    """Get the current app's login brute-force throttle."""
    return current_app.config["LOGIN_THROTTLE"]


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
