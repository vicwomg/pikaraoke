import datetime
import os
import subprocess
import sys
import threading
import time

import flask_babel
from flask import (
    Blueprint,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.current_app import get_admin_password, get_karaoke_instance, is_admin

_ = flask_babel.gettext


admin_bp = Blueprint("admin", __name__)


def delayed_halt(cmd: int, k: Karaoke):
    time.sleep(1.5)
    k.queue_clear()
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


@admin_bp.route("/update_ytdl")
def update_ytdl():
    k = get_karaoke_instance()

    def update_youtube_dl():
        time.sleep(3)
        k.upgrade_youtubedl()

    if is_admin():
        flash(
            # MSG: Message shown after starting the youtube-dl update.
            _("Updating youtube-dl! Should take a minute or two... "),
            "is-warning",
        )
        th = threading.Thread(target=update_youtube_dl)
        th.start()
    else:
        # MSG: Message shown after trying to update youtube-dl without admin permissions.
        flash(_("You don't have permission to update youtube-dl"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/refresh")
def refresh():
    k = get_karaoke_instance()
    if is_admin():
        k.get_available_songs()
    else:
        # MSG: Message shown after trying to refresh the song list without admin permissions.
        flash(_("You don't have permission to refresh the song list"), "is-danger")
    return redirect(url_for("files.browse"))


@admin_bp.route("/quit")
def quit():
    k = get_karaoke_instance()
    if is_admin():
        # MSG: Message shown after quitting pikaraoke.
        msg = _("Exiting pikaraoke now!")
        flash(msg, "is-danger")
        k.send_notification(msg, "danger")
        th = threading.Thread(target=delayed_halt, args=[0, k])
        th.start()
    else:
        # MSG: Message shown after trying to quit pikaraoke without admin permissions.
        flash(_("You don't have permission to quit"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/shutdown")
def shutdown():
    k = get_karaoke_instance()
    if is_admin():
        # MSG: Message shown after shutting down the system.
        msg = _("Shutting down system now!")
        flash(msg, "is-danger")
        k.send_notification(msg, "danger")
        th = threading.Thread(target=delayed_halt, args=[1, k])
        th.start()
    else:
        # MSG: Message shown after trying to shut down the system without admin permissions.
        flash(_("You don't have permission to shut down"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/reboot")
def reboot():
    k = get_karaoke_instance()
    if is_admin():
        # MSG: Message shown after rebooting the system.
        msg = _("Rebooting system now!")
        flash(msg, "is-danger")
        k.send_notification(msg, "danger")
        th = threading.Thread(target=delayed_halt, args=[2, k])
        th.start()
    else:
        # MSG: Message shown after trying to reboot the system without admin permissions.
        flash(_("You don't have permission to Reboot"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/expand_fs")
def expand_fs():
    k = get_karaoke_instance()
    if is_admin() and k.is_raspberry_pi:
        # MSG: Message shown after expanding the filesystem.
        flash(_("Expanding filesystem and rebooting system now!"), "is-danger")
        th = threading.Thread(target=delayed_halt, args=[3, k])
        th.start()
    elif not k.is_raspberry_pi:
        # MSG: Message shown after trying to expand the filesystem on a non-raspberry pi device.
        flash(_("Cannot expand fs on non-raspberry pi devices!"), "is-danger")
    else:
        # MSG: Message shown after trying to expand the filesystem without admin permissions
        flash(_("You don't have permission to resize the filesystem"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/auth", methods=["POST"])
def auth():
    d = request.form.to_dict()
    admin_password = get_admin_password()
    p = d["admin-password"]
    if p == admin_password:
        resp = make_response(redirect("/"))
        expire_date = datetime.datetime.now()
        expire_date = expire_date + datetime.timedelta(days=90)
        resp.set_cookie("admin", admin_password, expires=expire_date)
        # MSG: Message shown after logging in as admin successfully
        flash(_("Admin mode granted!"), "is-success")
    else:
        resp = make_response(redirect(url_for("login")))
        # MSG: Message shown after failing to login as admin
        flash(_("Incorrect admin password!"), "is-danger")
    return resp


@admin_bp.route("/login")
def login():
    return render_template("login.html")


@admin_bp.route("/logout")
def logout():
    resp = make_response(redirect("/"))
    resp.set_cookie("admin", "")
    # MSG: Message shown after logging out as admin successfully
    flash(_("Logged out of admin mode!"), "is-success")
    return resp
