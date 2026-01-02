"""Song queue management routes."""

import json

import flask_babel
from flask import Blueprint, flash, redirect, render_template, request, url_for

from pikaraoke.lib.current_app import (
    broadcast_event,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)

try:
    from urllib.parse import unquote
except ImportError:
    from urllib import unquote

_ = flask_babel.gettext

queue_bp = Blueprint("queue", __name__)


@queue_bp.route("/queue")
def queue():
    """Queue management page.
    ---
    tags:
      - Pages
    responses:
      200:
        description: HTML queue management page
    """
    k = get_karaoke_instance()
    site_name = get_site_name()
    return render_template(
        "queue.html",
        queue=k.queue,
        site_title=site_name,
        title="Queue",
        admin=is_admin(),
    )


@queue_bp.route("/get_queue")
def get_queue():
    """Get the current song queue.
    ---
    tags:
      - Queue
    responses:
      200:
        description: List of songs in queue
        schema:
          type: array
          items:
            type: object
            properties:
              user:
                type: string
                description: User who added the song
              file:
                type: string
                description: File path of the song
              title:
                type: string
                description: Display title of the song
              semitones:
                type: integer
                description: Transpose value in semitones
    """
    k = get_karaoke_instance()
    if len(k.queue) >= 1:
        return json.dumps(k.queue)
    else:
        return json.dumps([])


@queue_bp.route("/queue/addrandom", methods=["GET"])
def add_random():
    """Add random songs to the queue.
    ---
    tags:
      - Queue
    parameters:
      - name: amount
        in: query
        type: integer
        required: true
        description: Number of random songs to add
    responses:
      302:
        description: Redirects to queue page
    """
    k = get_karaoke_instance()
    amount = int(request.args["amount"])
    rc = k.queue_add_random(amount)
    if rc:
        # MSG: Message shown after adding random tracks
        flash(_("Added %s random tracks") % amount, "is-success")
    else:
        # MSG: Message shown after running out songs to add during random track addition
        flash(_("Ran out of songs!"), "is-warning")
    broadcast_event("queue_update")
    return redirect(url_for("queue.queue"))


@queue_bp.route("/queue/edit", methods=["GET"])
def queue_edit():
    k = get_karaoke_instance()
    action = request.args["action"]
    success = False
    if action == "clear":
        k.queue_clear()
        # MSG: Message shown after clearing the queue
        flash(_("Cleared the queue!"), "is-warning")
        broadcast_event("skip", "clear queue")
        success = True
    else:
        song = request.args["song"]
        song = unquote(song)
        if action == "down":
            result = k.queue_edit(song, "down")
            if result:
                # MSG: Message shown after moving a song down in the queue
                flash(_("Moved down in queue") + ": " + song, "is-success")
                success = True
            else:
                # MSG: Message shown after failing to move a song down in the queue
                flash(_("Error moving down in queue") + ": " + song, "is-danger")
        elif action == "up":
            result = k.queue_edit(song, "up")
            if result:
                # MSG: Message shown after moving a song up in the queue
                flash(_("Moved up in queue") + ": " + song, "is-success")
                success = True
            else:
                # MSG: Message shown after failing to move a song up in the queue
                flash(_("Error moving up in queue") + ": " + song, "is-danger")
        elif action == "delete":
            result = k.queue_edit(song, "delete")
            if result:
                # MSG: Message shown after deleting a song from the queue
                flash(_("Deleted from queue") + ": " + song, "is-success")
                success = True
            else:
                # MSG: Message shown after failing to delete a song from the queue
                flash(_("Error deleting from queue") + ": " + song, "is-danger")
    if success:
        broadcast_event("queue_update")
    return redirect(url_for("queue.queue"))


@queue_bp.route("/enqueue", methods=["POST", "GET"])
def enqueue():
    """Add a song to the queue.
    ---
    tags:
      - Queue
    parameters:
      - name: song
        in: query
        type: string
        description: Path to the song file
      - name: user
        in: query
        type: string
        description: Name of the user adding the song
      - name: song-to-add
        in: formData
        type: string
        description: Path to the song file (form data)
      - name: song-added-by
        in: formData
        type: string
        description: Name of the user (form data)
    responses:
      200:
        description: Result of enqueue operation
        schema:
          type: object
          properties:
            song:
              type: string
              description: Title of the song
            success:
              type: boolean
              description: Whether the song was added
    """
    k = get_karaoke_instance()
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
    rc = k.enqueue(song, user)
    broadcast_event("queue_update")
    song_title = k.filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})
