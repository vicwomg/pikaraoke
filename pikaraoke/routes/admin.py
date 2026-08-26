"""Admin routes for system control and authentication."""

import os
import subprocess
import sys
import threading
import time

import flask_babel
from flask import flash, jsonify, redirect, session, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.auth import host_only, public
from pikaraoke.lib.current_app import get_admin_auth, get_karaoke_instance
from pikaraoke.lib.youtube_dl import get_youtubedl_version, upgrade_youtubedl

_ = flask_babel.gettext
_lazy = flask_babel.lazy_gettext

admin_bp = Blueprint("admin", __name__)


class AdminPasswordForm(Schema):
    admin_password = fields.String(
        load_default="", metadata={"description": "New admin password; empty clears it"}
    )


class AuthForm(Schema):
    admin_password = fields.String(load_default="", metadata={"description": "Admin password"})
    next = fields.String(
        load_default="/", metadata={"description": "URL to redirect to after login"}
    )


def delayed_halt(cmd: int, k: Karaoke):
    time.sleep(1.5)
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


@admin_bp.route("/update_ytdl", methods=["POST"])
# MSG: Message shown after trying to update yt-dlp without admin permissions.
@host_only(_lazy("You don't have permission to update yt-dlp"))
def update_ytdl():
    """Update yt-dlp to the latest version."""
    k = get_karaoke_instance()

    def update_youtube_dl():
        time.sleep(3)
        k.youtubedl_version = upgrade_youtubedl()

    flash(
        # MSG: Message shown after starting the yt-dlp update.
        _("Updating yt-dlp! Should take a minute or two... "),
        "is-warning",
    )
    th = threading.Thread(target=update_youtube_dl)
    th.start()
    return redirect(url_for("info.info"))


@admin_bp.route("/library_stats")
@host_only(json=True)
def library_stats():
    """Return song count for the admin dashboard."""
    k = get_karaoke_instance()
    return jsonify({"song_count": len(k.song_manager.songs)})


@admin_bp.route("/sync_library", methods=["POST"])
@host_only(json=True)
def sync_library():
    """Trigger a background library scan."""
    k = get_karaoke_instance()
    started = k.sync_library()
    if started:
        return jsonify({"status": "started"})
    return jsonify({"status": "already_syncing"})


@admin_bp.route("/quit", methods=["POST"])
# MSG: Message shown after trying to quit pikaraoke without admin permissions.
@host_only(_lazy("You don't have permission to quit"))
def quit():
    """Exit the PiKaraoke application."""
    k = get_karaoke_instance()
    msg = _("Exiting pikaraoke now!")
    flash(msg, "is-danger")
    k.send_notification(msg, "danger")
    th = threading.Thread(target=delayed_halt, args=[0, k])
    th.start()
    return redirect(url_for("home.home"))


@admin_bp.route("/shutdown", methods=["POST"])
# MSG: Message shown after trying to shut down the system without admin permissions.
@host_only(_lazy("You don't have permission to shut down"))
def shutdown():
    """Shut down the host system."""
    k = get_karaoke_instance()
    msg = _("Shutting down system now!")
    flash(msg, "is-danger")
    k.send_notification(msg, "danger")
    th = threading.Thread(target=delayed_halt, args=[1, k])
    th.start()
    return redirect(url_for("home.home"))


@admin_bp.route("/reboot", methods=["POST"])
# MSG: Message shown after trying to reboot the system without admin permissions.
@host_only(_lazy("You don't have permission to Reboot"))
def reboot():
    """Reboot the host system."""
    k = get_karaoke_instance()
    msg = _("Rebooting system now!")
    flash(msg, "is-danger")
    k.send_notification(msg, "danger")
    th = threading.Thread(target=delayed_halt, args=[2, k])
    th.start()
    return redirect(url_for("home.home"))


@admin_bp.route("/expand_fs", methods=["POST"])
# MSG: Message shown after trying to expand the filesystem without admin permissions
@host_only(_lazy("You don't have permission to resize the filesystem"))
def expand_fs():
    """Expand filesystem on Raspberry Pi."""
    k = get_karaoke_instance()
    if k.is_raspberry_pi:
        # MSG: Message shown after expanding the filesystem.
        flash(_("Expanding filesystem and rebooting system now!"), "is-danger")
        th = threading.Thread(target=delayed_halt, args=[3, k])
        th.start()
    else:
        # MSG: Message shown after trying to expand the filesystem on a non-raspberry pi device.
        flash(_("Cannot expand fs on non-raspberry pi devices!"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/auth", methods=["POST"])
@public
@admin_bp.arguments(AuthForm, location="form")
def auth(form):
    """Authenticate as admin."""
    next_url = form["next"]

    # Validate next_url to prevent open redirect vulnerabilities
    if not next_url.startswith("/"):
        next_url = "/"

    admin_auth = get_admin_auth()
    if admin_auth.verify(form["admin_password"]):
        session["admin"] = admin_auth.session_token
        session.permanent = True
        # MSG: Message shown after logging in as admin successfully
        flash(_("Admin mode granted!"), "is-success")
    else:
        # MSG: Message shown after failing to login as admin
        flash(_("Incorrect admin password!"), "is-danger")
    return redirect(next_url)


@admin_bp.route("/admin_password", methods=["POST"])
# MSG: Message shown after trying to change the admin password without admin permissions.
@host_only(_lazy("You don't have permission to change the admin password"))
@admin_bp.arguments(AdminPasswordForm, location="form")
def set_admin_password(form):
    """Set, change or clear the admin password without restarting."""

    # No current-password field: an admin session can already shut the box down.
    password = form["admin_password"]
    admin_auth = get_admin_auth()
    admin_auth.set_password(password)
    if password:
        # set_password logged every device out; keep the one that just set it.
        session["admin"] = admin_auth.session_token
        session.permanent = True
        # MSG: Message shown after setting a new admin password.
        flash(_("Admin password set. Other devices will need to log in again."), "is-success")
    else:
        # MSG: Message shown after clearing the admin password, making everyone an admin.
        flash(_("Admin password cleared. Everyone is an admin again."), "is-warning")
    return redirect(url_for("info.info"))


@admin_bp.route("/logout", methods=["POST"])
# Public though the button is admin-only: it clears the caller's own
# session and grants nothing, so gating it would refuse a no-op.
@public
def logout():
    """Log out of admin mode."""
    session.pop("admin", None)
    # MSG: Message shown after logging out as admin successfully
    flash(_("Logged out of admin mode!"), "is-success")
    return redirect(url_for("info.info"))
