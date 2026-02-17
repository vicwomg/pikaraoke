"""Song queue management routes."""

import json
from urllib.parse import unquote

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import (
    broadcast_event,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)

_ = flask_babel.gettext

queue_bp = Blueprint("queue", __name__)


@queue_bp.route("/queue")
def queue():
    """Queue management page."""
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
    """Get the current song queue."""
    k = get_karaoke_instance()
    return json.dumps(k.queue_manager.queue)


@queue_bp.route("/queue/addrandom", methods=["GET"])
@queue_bp.doc(
    parameters=[
        {
            "name": "amount",
            "in": "query",
            "schema": {"type": "integer", "default": 1},
            "description": "Number of random songs to add",
        },
    ]
)
def add_random():
    """Add random songs to the queue."""
    k = get_karaoke_instance()
    amount = request.args.get("amount", default=1, type=int)
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
@queue_bp.doc(
    parameters=[
        {
            "name": "old_index",
            "in": "formData",
            "schema": {"type": "integer"},
            "required": True,
            "description": "The current index of the item to move",
        },
        {
            "name": "new_index",
            "in": "formData",
            "schema": {"type": "integer"},
            "required": True,
            "description": "The target index to move the item to",
        },
    ]
)
def reorder():
    """Handle drag-and-drop reordering of the queue."""
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
@queue_bp.doc(
    parameters=[
        {
            "name": "action",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": ["clear", "top", "bottom", "up", "down", "delete"],
            },
            "required": True,
            "description": "Queue edit action to perform",
        },
        {
            "name": "song",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Path to the song file (required unless action is 'clear')",
        },
    ]
)
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
        song_title = k.song_manager.filename_from_path(song)

        # MSG labels for each action
        success_labels = {
            "top": _("Moved to top of queue"),
            "bottom": _("Moved to bottom of queue"),
            "up": _("Moved up in queue"),
            "down": _("Moved down in queue"),
            "delete": _("Deleted from queue"),
        }
        error_labels = {
            "top": _("Error moving to top of queue"),
            "bottom": _("Error moving to bottom of queue"),
            "up": _("Error moving up in queue"),
            "down": _("Error moving down in queue"),
            "delete": _("Error deleting from queue"),
        }

        if action == "top":
            success = k.queue_manager.move_to_top(song)
        elif action == "bottom":
            success = k.queue_manager.move_to_bottom(song)
        else:
            success = k.queue_manager.queue_edit(song, action)

        if action in success_labels:
            message = (
                (success_labels[action] if success else error_labels[action]) + ": " + song_title
            )

        if message and not is_ajax:
            flash(message, "is-success" if success else "is-danger")

    # Note: No need to manually emit events here - all QueueManager methods
    # (queue_clear, queue_edit, reorder) already emit queue_update and now_playing_update events

    if is_ajax:
        return json.dumps({"success": success, "message": message})
    return redirect(url_for("queue.queue"))


@queue_bp.route("/enqueue", methods=["POST", "GET"])
@queue_bp.doc(
    parameters=[
        {
            "name": "song",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Path to the song file",
        },
        {
            "name": "user",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Name of the user adding the song",
        },
        {
            "name": "song-to-add",
            "in": "formData",
            "schema": {"type": "string"},
            "description": "Path to the song file (form data)",
        },
        {
            "name": "song-added-by",
            "in": "formData",
            "schema": {"type": "string"},
            "description": "Name of the user (form data)",
        },
    ]
)
def enqueue():
    """Add a song to the queue."""
    k = get_karaoke_instance()
    song = request.args.get("song") or request.form["song-to-add"]
    user = request.args.get("user") or request.form["song-added-by"]
    rc = k.queue_manager.enqueue(song, user)
    broadcast_event("queue_update")
    song_title = k.song_manager.filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})


@queue_bp.route("/queue/downloads")
def get_current_downloads():
    """Get the status of current and pending downloads."""
    k = get_karaoke_instance()
    return json.dumps(k.download_manager.get_downloads_status())


@queue_bp.route("/queue/downloads/errors/<error_id>", methods=["DELETE"])
def delete_download_error(error_id):
    """Remove a download error from the list."""
    k = get_karaoke_instance()
    if k.download_manager.remove_error(error_id):
        return json.dumps({"success": True})
    return json.dumps({"success": False, "error": "Error not found"}), 404
