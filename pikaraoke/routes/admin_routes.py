import os
import subprocess
import sys
import threading
import time

import cherrypy
import psutil
from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from yt_dlp.version import __version__ as yt_dlp_version

from pikaraoke import Karaoke, __version__, get_current_app, is_admin

admin_bp = Blueprint("admin", __name__)


# Delay system commands to allow redirect to render first
def _delayed_halt(karaoke: Karaoke, cmd: int):
    time.sleep(1.5)
    karaoke.queue_clear()
    cherrypy.engine.stop()
    cherrypy.engine.exit()
    karaoke.stop()
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


@admin_bp.route("/refresh")
def refresh():
    current_app = get_current_app()

    if is_admin(current_app.config["ADMIN_PASSWORD"]):
        current_app.karaoke.get_available_songs()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("home.browse"))


@admin_bp.route("/quit")
def quit():
    current_app = get_current_app()

    if is_admin(current_app.config["ADMIN_PASSWORD"]):
        flash("Quitting pikaraoke now!", "is-warning")
        th = threading.Thread(target=_delayed_halt, args=[current_app.karaoke, 0])
        th.start()
    else:
        flash("You don't have permission to quit", "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/shutdown")
def shutdown():
    if is_admin(current_app.config["ADMIN_PASSWORD"]):
        flash("Shutting down system now!", "is-danger")
        th = threading.Thread(target=_delayed_halt, args=[1])
        th.start()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/reboot")
def reboot():
    if is_admin(current_app.config["ADMIN_PASSWORD"]):
        flash("Rebooting system now!", "is-danger")
        th = threading.Thread(target=_delayed_halt, args=[2])
        th.start()
    else:
        flash("You don't have permission to Reboot", "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/expand_fs")
def expand_fs():
    current_app = get_current_app()

    if is_admin(current_app.config["ADMIN_PASSWORD"]) and current_app.platform.is_rpi():
        flash("Expanding filesystem and rebooting system now!", "is-danger")
        th = threading.Thread(target=_delayed_halt, args=[current_app, 3])
        th.start()
    elif not current_app.platform.is_rpi():
        flash("Cannot expand fs on non-raspberry pi devices!", "is-danger")
    else:
        flash("You don't have permission to resize the filesystem", "is-danger")

    return redirect(url_for("home.home"))


@admin_bp.route("/info")
def info():
    current_app = get_current_app()
    url = current_app.karaoke.url

    # cpu
    cpu = str(psutil.cpu_percent()) + "%"

    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available / 1024.0 / 1024.0, 1)
    total = round(memory.total / 1024.0 / 1024.0, 1)
    memory = (
        str(available) + "MB free / " + str(total) + "MB total ( " + str(memory.percent) + "% )"
    )

    # disk
    disk = psutil.disk_usage("/")
    # Divide from Bytes -> KB -> MB -> GB
    free = round(disk.free / 1024.0 / 1024.0 / 1024.0, 1)
    total = round(disk.total / 1024.0 / 1024.0 / 1024.0, 1)
    disk = str(free) + "GB free / " + str(total) + "GB total ( " + str(disk.percent) + "% )"

    return render_template(
        "info.html",
        site_title=current_app.config["SITE_NAME"],
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        youtubedl_version=yt_dlp_version,
        is_pi=current_app.platform.is_rpi(),
        pikaraoke_version=__version__,
        admin=is_admin(current_app.config["ADMIN_PASSWORD"]),
        admin_enabled=current_app.config["ADMIN_PASSWORD"] != None,
    )


@admin_bp.route("/update_ytdl")
def update_ytdl():
    # Support for updating ytdl removed
    return redirect(url_for("admin.info"))
