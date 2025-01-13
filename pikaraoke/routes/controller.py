import flask_babel
from flask import Blueprint, redirect, request, url_for

from pikaraoke.lib.current_app import broadcast_event, get_karaoke_instance

_ = flask_babel.gettext


controller_bp = Blueprint("controller", __name__)


@controller_bp.route("/skip")
def skip():
    k = get_karaoke_instance()
    broadcast_event("skip", "user command")
    k.skip()
    return redirect(url_for("home.home"))


@controller_bp.route("/pause")
def pause():
    k = get_karaoke_instance()
    if k.is_paused:
        broadcast_event("play")
    else:
        broadcast_event("pause")
    k.pause()
    return redirect(url_for("home.home"))


@controller_bp.route("/transpose/<semitones>", methods=["GET"])
def transpose(semitones):
    k = get_karaoke_instance()
    broadcast_event("skip", "transpose current")
    k.transpose_current(int(semitones))
    return redirect(url_for("home.home"))


@controller_bp.route("/restart")
def restart():
    k = get_karaoke_instance()
    broadcast_event("restart")
    k.restart()
    return redirect(url_for("home.home"))


@controller_bp.route("/volume/<volume>")
def volume(volume):
    k = get_karaoke_instance()
    broadcast_event("volume", volume)
    k.volume_change(float(volume))
    return redirect(url_for("home.home"))


@controller_bp.route("/vol_up")
def vol_up():
    k = get_karaoke_instance()
    broadcast_event("volume", "up")
    k.vol_up()
    return redirect(url_for("home.home"))


@controller_bp.route("/vol_down")
def vol_down():
    k = get_karaoke_instance()
    broadcast_event("volume", "down")
    k.vol_down()
    return redirect(url_for("home.home"))
