"""Playback control routes for skip, pause, volume, and transpose."""

import flask_babel
from flask import Blueprint, redirect, request, url_for

from pikaraoke.lib.current_app import broadcast_event, get_karaoke_instance

_ = flask_babel.gettext


controller_bp = Blueprint("controller", __name__)


@controller_bp.route("/skip")
def skip():
    """Skip the currently playing song.
    ---
    tags:
      - Playback
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    broadcast_event("skip", "user command")
    k.skip()
    return redirect(url_for("home.home"))


@controller_bp.route("/pause")
def pause():
    """Toggle pause/resume playback.
    ---
    tags:
      - Playback
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    if k.is_paused:
        broadcast_event("play")
    else:
        broadcast_event("pause")
    k.pause()
    return redirect(url_for("home.home"))


@controller_bp.route("/transpose/<semitones>", methods=["GET"])
def transpose(semitones):
    """Transpose (pitch shift) the current song.
    ---
    tags:
      - Playback
    parameters:
      - name: semitones
        in: path
        type: integer
        required: true
        description: Semitones to transpose (-12 to 12)
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    broadcast_event("skip", "transpose current")
    k.transpose_current(int(semitones))
    return redirect(url_for("home.home"))


@controller_bp.route("/restart")
def restart():
    """Restart the current song from the beginning.
    ---
    tags:
      - Playback
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    broadcast_event("restart")
    k.restart()
    return redirect(url_for("home.home"))


@controller_bp.route("/volume/<volume>")
def volume(volume):
    """Set the playback volume.
    ---
    tags:
      - Playback
    parameters:
      - name: volume
        in: path
        type: number
        required: true
        description: Volume level (0.0 to 1.0)
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    broadcast_event("volume", volume)
    k.volume_change(float(volume))
    return redirect(url_for("home.home"))


@controller_bp.route("/vol_up")
def vol_up():
    """Increase volume by 10%.
    ---
    tags:
      - Playback
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    broadcast_event("volume", "up")
    k.vol_up()
    return redirect(url_for("home.home"))


@controller_bp.route("/vol_down")
def vol_down():
    """Decrease volume by 10%.
    ---
    tags:
      - Playback
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    broadcast_event("volume", "down")
    k.vol_down()
    return redirect(url_for("home.home"))
