"""Song queue management routes."""

from __future__ import annotations

import json
from urllib.parse import unquote

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import (
    broadcast_event,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)

_ = flask_babel.gettext

queue_bp = Blueprint("queue", __name__)


class ReorderForm(Schema):
    old_index = fields.Integer(
        required=True, metadata={"description": "Current index of the item to move"}
    )
    new_index = fields.Integer(
        required=True, metadata={"description": "Target index to move the item to"}
    )


class EnqueueQuery(Schema):
    song = fields.String(required=True, metadata={"description": "Path to the song file"})
    user = fields.String(
        load_default="", metadata={"description": "Name of the user adding the song"}
    )


class EnqueueForm(Schema):
    song_to_add = fields.String(required=True, metadata={"description": "Path to the song file"})
    song_added_by = fields.String(
        load_default="", metadata={"description": "Name of the user adding the song"}
    )


class QueueEditQuery(Schema):
    action = fields.String(required=True, metadata={"description": "Queue edit action to perform"})
    song = fields.String(
        metadata={"description": "Path to the song file (required unless action is 'clear')"}
    )


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


@queue_bp.route("/queue/addrandom/<int:amount>", methods=["GET"])
def add_random(amount):
    """Add random songs to the queue."""
    k = get_karaoke_instance()
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
@queue_bp.arguments(ReorderForm, location="form")
def reorder(form):
    """Handle drag-and-drop reordering of the queue."""
    if not is_admin():
        return json.dumps({"success": False, "error": "Unauthorized"}), 403

    k = get_karaoke_instance()
    try:
        success = k.queue_manager.reorder(form["old_index"], form["new_index"])
        return json.dumps({"success": success})
    except (ValueError, IndexError):
        pass

    return json.dumps({"success": False})


@queue_bp.route("/queue/edit", methods=["GET"])
@queue_bp.arguments(QueueEditQuery, location="query")
def queue_edit(query):
    """Edit queue items (admin only)."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not is_admin():
        if is_ajax:
            return json.dumps({"success": False, "error": "Unauthorized"}), 403
        # MSG: Message shown when non-admin tries to edit queue
        flash(_("Unauthorized"), "is-danger")
        return redirect(url_for("queue.queue"))

    k = get_karaoke_instance()
    action = query["action"]
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
        song = unquote(query.get("song", ""))
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


def _do_enqueue(song: str, user: str) -> str:
    k = get_karaoke_instance()
    rc = k.queue_manager.enqueue(song, user)
    broadcast_event("queue_update")
    song_title = k.song_manager.filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})


@queue_bp.route("/enqueue", methods=["GET"])
@queue_bp.arguments(EnqueueQuery, location="query")
def enqueue(query):
    """Add a song to the queue (used by the file browser)."""
    return _do_enqueue(query["song"], query["user"])


@queue_bp.route("/enqueue", methods=["POST"])
@queue_bp.arguments(EnqueueForm, location="form")
def enqueue_form(form):
    """Add a song to the queue (used by the search page)."""
    return _do_enqueue(form["song_to_add"], form["song_added_by"])


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
