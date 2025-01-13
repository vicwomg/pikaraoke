import logging
import os
import subprocess
import sys
import time

from flask import current_app, request
from flask_socketio import emit

from pikaraoke.karaoke import Karaoke


def is_admin() -> bool:
    """Determine if the current app's admin password matches the admin cookie value
    This function checks if the provided password is `None` or if it matches
    the value of the "admin" cookie in the current Flask request. If the password
    is `None`, the function assumes the user is an admin. If the "admin" cookie
    is present and its value matches the provided password, the function returns `True`.
    Otherwise, it returns `False`.
    Returns:
        bool: `True` if the password matches the admin cookie or if the password is `None`,
              `False` otherwise.
    """
    password = get_admin_password()
    return password is None or request.cookies.get("admin") == password


def get_karaoke_instance() -> Karaoke:
    """Get the current app's Karaoke instance
    This function returns the Karaoke instance stored in the current app's configuration.
    Returns:
        Karaoke: The Karaoke instance stored in the current app's configuration.
    """
    return current_app.k


def get_admin_password() -> str:
    """Get the admin password from the current app's configuration
    This function returns the admin password stored in the current app's configuration.
    Returns:
        str: The admin password stored in the current app's configuration.
    """
    return current_app.config["ADMIN_PASSWORD"]


def get_site_name() -> str:
    """Get the site name from the current app's configuration
    This function returns the site name stored in the current app's configuration.
    Returns:
        str: The site name stored in the current app's configuration.
    """
    return current_app.config["SITE_NAME"]


def broadcast_event(event, data=None):
    logging.debug("Broadcasting event: " + event)
    emit(event, data, namespace="/", broadcast=True)


def delayed_halt(cmd):
    time.sleep(1.5)
    current_app.k.queue_clear()
    current_app.k.stop()
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
