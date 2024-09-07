import json
import logging
import threading
from urllib.parse import unquote

from flask import Blueprint, flash, redirect, request, url_for

from pikaraoke import filename_from_path, get_current_app, hash_dict
from pikaraoke.lib.logger import log_endpoint_access

karaoke_bp = Blueprint("karaoke", __name__)
logger = logging.getLogger(__name__)


@karaoke_bp.route("/nowplaying")
# @log_endpoint_access # Annoyingly a lot of requests
def nowplaying() -> str:
    current_app = get_current_app()
    try:
        if len(current_app.karaoke.queue) >= 1:
            next_song = current_app.karaoke.queue[0]["title"]
            next_user = current_app.karaoke.queue[0]["user"]
        else:
            next_song = None
            next_user = None
        rc = {
            "now_playing": current_app.karaoke.now_playing,
            "now_playing_user": current_app.karaoke.now_playing_user,
            "now_playing_command": current_app.karaoke.now_playing_command,
            "up_next": next_song,
            "next_user": next_user,
            "now_playing_url": current_app.karaoke.now_playing_url,
            "is_paused": current_app.karaoke.is_paused,
            "transpose_value": current_app.karaoke.now_playing_transpose,
            "volume": current_app.karaoke.volume,
        }
        rc["hash"] = hash_dict(rc)  # used to detect changes in the now playing data
        return json.dumps(rc)
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""


# Call this after receiving a command in the front end
@karaoke_bp.route("/clear_command")
@log_endpoint_access
def clear_command():
    current_app = get_current_app()
    current_app.karaoke.now_playing_command = None
    return ""


@karaoke_bp.route("/get_queue")
@log_endpoint_access
def get_queue() -> str:
    current_app = get_current_app()
    return json.dumps(current_app.karaoke.queue if len(current_app.karaoke.queue) >= 1 else [])


@karaoke_bp.route("/queue/addrandom", methods=["GET"])
@log_endpoint_access
def add_random():
    current_app = get_current_app()
    amount = int(request.args["amount"])
    rc = current_app.karaoke.queue_add_random(amount)
    if rc:
        flash("Added %s random tracks" % amount, "is-success")
    else:
        flash("Ran out of songs!", "is-warning")

    return redirect(url_for("home.queue"))


@karaoke_bp.route("/queue/edit", methods=["GET"])
@log_endpoint_access
def queue_edit():
    current_app = get_current_app()
    action = request.args["action"]
    if action == "clear":
        current_app.karaoke.queue_clear()
        flash("Cleared the queue!", "is-warning")
        return redirect(url_for("home.queue"))

    song = unquote(request.args["song"])
    if action == "down":
        if current_app.karaoke.queue_edit(song, "down"):
            flash("Moved down in queue: " + song, "is-success")
        else:
            flash("Error moving down in queue: " + song, "is-danger")
    elif action == "up":
        if current_app.karaoke.queue_edit(song, "up"):
            flash("Moved up in queue: " + song, "is-success")
        else:
            flash("Error moving up in queue: " + song, "is-danger")
    elif action == "delete":
        if current_app.karaoke.queue_edit(song, "delete"):
            flash("Deleted from queue: " + song, "is-success")
        else:
            flash("Error deleting from queue: " + song, "is-danger")

    return redirect(url_for("home.queue"))


@karaoke_bp.route("/enqueue", methods=["POST", "GET"])
@log_endpoint_access
def enqueue():
    current_app = get_current_app()
    if "song" in request.args:
        song = request.args["song"]
    else:
        d = request.form.to_dict()
        song = d["song-to-add"]
    if "user" in request.args:
        user = request.args["user"]
    else:
        d = request.form.to_dict()
        user = d["song-added-by"]
    rc = current_app.karaoke.enqueue(song, user)
    song_title = filename_from_path(song)

    return json.dumps({"song": song_title, "success": rc})


@karaoke_bp.route("/skip")
@log_endpoint_access
def skip():
    current_app = get_current_app()
    current_app.karaoke.skip()
    return redirect(url_for("home.home"))


@karaoke_bp.route("/pause")
@log_endpoint_access
def pause():
    current_app = get_current_app()
    current_app.karaoke.pause()
    return redirect(url_for("home.home"))


@karaoke_bp.route("/transpose/<semitones>", methods=["GET"])
@log_endpoint_access
def transpose(semitones):
    current_app = get_current_app()
    current_app.karaoke.transpose_current(int(semitones))
    return redirect(url_for("home.home"))


@karaoke_bp.route("/restart")
@log_endpoint_access
def restart():
    current_app = get_current_app()
    current_app.karaoke.restart()
    return redirect(url_for("home.home"))


@karaoke_bp.route("/volume/<volume>")
@log_endpoint_access
def volume(volume):
    current_app = get_current_app()
    current_app.karaoke.volume_change(float(volume))
    return redirect(url_for("home.home"))


@karaoke_bp.route("/vol_up")
@log_endpoint_access
def vol_up():
    current_app = get_current_app()
    current_app.karaoke.vol_up()
    return redirect(url_for("home.home"))


@karaoke_bp.route("/vol_down")
@log_endpoint_access
def vol_down():
    current_app = get_current_app()
    current_app.karaoke.vol_down()
    return redirect(url_for("home.home"))


@karaoke_bp.route("/download", methods=["POST"])
@log_endpoint_access
def download():
    current_app = get_current_app()
    d = request.form.to_dict()
    current_app.logger.debug(f"Got download request: {d=}")
    song = d["song-url"]
    user = d["song-added-by"]

    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=current_app.karaoke.download_video, args=[song, queue, user])
    t.daemon = True
    t.start()

    flash_message = (
        "Download started: '" + song + "'. This may take a couple of minutes to complete. "
    )

    if queue:
        flash_message += "Song will be added to queue."
    else:
        flash_message += 'Song will appear in the "available songs" list.'
    flash(flash_message, "is-info")
    return redirect(url_for("home.search"))


@karaoke_bp.route("/end_song", methods=["GET"])
@log_endpoint_access
def end_song():
    current_app = get_current_app()
    current_app.karaoke.end_song()
    return "ok"


@karaoke_bp.route("/start_song", methods=["GET"])
@log_endpoint_access
def start_song():
    current_app = get_current_app()
    current_app.karaoke.start_song()
    return "ok"


@karaoke_bp.route("/autocomplete")
@log_endpoint_access
def autocomplete():
    current_app = get_current_app()

    q = request.args.get("q").lower()
    result = [
        {
            "path": song,
            "fileName": current_app.karaoke.filename_from_path(song),
            "type": "autocomplete",
        }
        for song in current_app.karaoke.available_songs
        if q in song.lower()
    ]
    response = current_app.response_class(response=json.dumps(result), mimetype="application/json")

    current_app.logger.debug(f"Autocomplete response: {result}")
    return response
