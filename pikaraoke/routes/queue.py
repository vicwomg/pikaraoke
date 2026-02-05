"""Song queue management routes."""

import json
from urllib.parse import unquote

import flask_babel
from flask import Blueprint, flash, redirect, render_template, request, url_for

from pikaraoke.lib.current_app import (
    broadcast_event,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)

_ = flask_babel.gettext

queue_bp = Blueprint("queue", __name__)


def _find_song_index(queue: list, song_name: str) -> int:
    """Find a song's index in the queue by partial filename match. Returns -1 if not found."""
    for i, item in enumerate(queue):
        if song_name in item["file"]:
            return i
    return -1


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
        queue=k.queue_manager.queue,
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
    return json.dumps(k.queue_manager.queue)


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
    rc = k.queue_manager.queue_add_random(amount)
    if rc:
        # MSG: Message shown after adding random tracks
        flash(_("Added %s random tracks") % amount, "is-success")
    else:
        # MSG: Message shown after running out songs to add during random track addition
        flash(_("Ran out of songs!"), "is-warning")
    broadcast_event("queue_update")
    return redirect(url_for("queue.queue"))


@queue_bp.route("/queue/reorder", methods=["POST"])
def reorder():
    """Handle drag-and-drop reordering of the queue.
    ---
    tags:
      - Queue
    consumes:
      - application/x-www-form-urlencoded
    parameters:
      - name: old_index
        in: formData
        type: integer
        required: true
        description: The current index of the item to move
      - name: new_index
        in: formData
        type: integer
        required: true
        description: The target index to move the item to
    responses:
      200:
        description: Result of the reorder operation
        schema:
          type: object
          properties:
            success:
              type: boolean
              description: Whether the reorder was successful
            error:
              type: string
              description: Error message if failed
      403:
        description: Unauthorized access (admin only)
    """
    if not is_admin():
        return json.dumps({"success": False, "error": "Unauthorized"}), 403

    k = get_karaoke_instance()
    try:
        old_index = int(request.form["old_index"])
        new_index = int(request.form["new_index"])

        success = k.queue_manager.reorder(old_index, new_index)
        return json.dumps({"success": success})
    except (ValueError, IndexError):
        pass

    return json.dumps({"success": False})


@queue_bp.route("/queue/edit", methods=["GET"])
def queue_edit():
    """Edit queue items (admin only)."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not is_admin():
        if is_ajax:
            return json.dumps({"success": False, "error": "Unauthorized"}), 403
        # MSG: Message shown when non-admin tries to edit queue
        flash(_("Unauthorized"), "is-danger")
        return redirect(url_for("queue.queue"))

    k = get_karaoke_instance()
    action = request.args["action"]
    success = False
    message = ""

    if action == "clear":
        k.queue_manager.queue_clear()
        message = _("Cleared the queue!")
        if not is_ajax:
            # MSG: Message shown after clearing the queue
            flash(message, "is-warning")
        broadcast_event("skip", "clear queue")
        success = True
    else:
        song = unquote(request.args["song"])
        song_title = k.filename_from_path(song)

        if action == "top":
            found_index = _find_song_index(k.queue_manager.queue, song)
            if found_index > 0:
                success = k.queue_manager.reorder(found_index, 0)
                if success:
                    message = _("Moved to top of queue") + ": " + song_title

        elif action == "bottom":
            found_index = _find_song_index(k.queue_manager.queue, song)
            if 0 <= found_index < len(k.queue_manager.queue) - 1:
                success = k.queue_manager.reorder(found_index, len(k.queue_manager.queue) - 1)
                if success:
                    message = _("Moved to bottom of queue") + ": " + song_title

        elif action in ("up", "down", "delete"):
            # MSG labels for each action
            success_labels = {
                "up": _("Moved up in queue"),
                "down": _("Moved down in queue"),
                "delete": _("Deleted from queue"),
            }
            error_labels = {
                "up": _("Error moving up in queue"),
                "down": _("Error moving down in queue"),
                "delete": _("Error deleting from queue"),
            }
            success = k.queue_manager.queue_edit(song, action)
            if success:
                message = success_labels[action] + ": " + song_title
            else:
                message = error_labels[action] + ": " + song_title

        if message and not is_ajax:
            flash(message, "is-success" if success else "is-danger")

    # Note: No need to manually emit events here - all QueueManager methods
    # (queue_clear, queue_edit, reorder) already emit queue_update and now_playing_update events

    if is_ajax:
        return json.dumps({"success": success, "message": message})
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
    song = request.args.get("song") or request.form["song-to-add"]
    user = request.args.get("user") or request.form["song-added-by"]
    rc = k.queue_manager.enqueue(song, user)
    broadcast_event("queue_update")
    song_title = k.filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})


@queue_bp.route("/queue/downloads")
def get_current_downloads():
    """Get the status of current and pending downloads.
    ---
    tags:
      - Queue
    responses:
      200:
        description: Status of active and pending downloads
        schema:
          type: object
          properties:
            active:
              type: object
              description: Currently active download info
            pending:
              type: array
              items:
                type: object
                description: Pending download info
            errors:
              type: array
              items:
                type: object
                description: Failed download info
    """
    k = get_karaoke_instance()
    return json.dumps(k.download_manager.get_downloads_status())


@queue_bp.route("/queue/downloads/errors/<error_id>", methods=["DELETE"])
def delete_download_error(error_id):
    """Remove a download error from the list.
    ---
    tags:
      - Queue
    parameters:
      - name: error_id
        in: path
        type: string
        required: true
        description: ID of the error to remove
    responses:
      200:
        description: Error removed successfully
      404:
        description: Error not found
    """
    k = get_karaoke_instance()
    if k.download_manager.remove_error(error_id):
        return json.dumps({"success": True})
    else:
        return json.dumps({"success": False, "error": "Error not found"}), 404
