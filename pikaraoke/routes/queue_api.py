"""API for the song queue and the downloads feeding it."""

from urllib.parse import unquote

import flask_babel
from flask import Response, jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import broadcast_event, get_karaoke_instance

_ = flask_babel.gettext

queue_api_bp = Blueprint("queue_api", __name__)


class ReorderForm(Schema):
    old_index = fields.Integer(
        required=True, metadata={"description": "Current index of the item to move"}
    )
    new_index = fields.Integer(
        required=True, metadata={"description": "Target index to move the item to"}
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


class QueuePitchQuery(Schema):
    song = fields.String(required=True, metadata={"description": "Path to the song file"})
    semitones = fields.Integer(
        required=True, metadata={"description": "Key the song will transcode in when it starts"}
    )


@queue_api_bp.route("/api/get_queue")
@public
def get_queue():
    """Get the current song queue."""
    k = get_karaoke_instance()
    return jsonify(k.queue_manager.queue)


@queue_api_bp.route("/api/queue/addrandom/<int:amount>", methods=["POST"])
def add_random(amount):
    """Add random songs to the queue.

    The queue redraws itself off `queue_update`, so only the empty-handed case
    needs saying.
    """
    k = get_karaoke_instance()
    added = k.queue_manager.queue_add_random(amount)
    broadcast_event("queue_update")
    # MSG: Message shown after running out songs to add during random track addition
    message = "" if added else _("Ran out of songs!")
    return jsonify({"success": added, "message": message})


@queue_api_bp.route("/api/queue/reorder", methods=["POST"])
@queue_api_bp.arguments(ReorderForm, location="form")
def reorder(form):
    """Handle drag-and-drop reordering of the queue."""
    k = get_karaoke_instance()
    try:
        success = k.queue_manager.reorder(form["old_index"], form["new_index"])
        return jsonify({"success": success})
    except (ValueError, IndexError):
        pass

    return jsonify({"success": False})


@queue_api_bp.route("/api/queue/edit", methods=["POST"])
@queue_api_bp.arguments(QueueEditQuery, location="query")
def queue_edit(query):
    """Edit queue items (admin only)."""
    k = get_karaoke_instance()
    action = query["action"]

    if action == "clear":
        k.queue_manager.queue_clear()
        broadcast_event("skip", "clear queue")
        return jsonify({"success": True})

    song = unquote(query.get("song", ""))
    if action == "top":
        success = k.queue_manager.move_to_top(song)
    elif action == "bottom":
        success = k.queue_manager.move_to_bottom(song)
    else:
        success = k.queue_manager.queue_edit(song, action)

    # QueueManager emits queue_update and now_playing_update itself, and Karaoke
    # bridges those to the socket -- so this path adds no broadcast_event.
    return jsonify({"success": success})


@queue_api_bp.route("/api/queue/pitch", methods=["POST"])
@queue_api_bp.arguments(QueuePitchQuery, location="query")
def queue_pitch(query):
    """Pre-set the key a queued song transcodes in when it starts (admin only).

    QueueManager emits now_playing_update itself, which Karaoke bridges to the
    socket, so this path adds no broadcast_event.
    """
    k = get_karaoke_instance()
    success = k.queue_manager.set_song_semitones(unquote(query["song"]), query["semitones"])
    return jsonify({"success": success})


def _do_enqueue(song: str, user: str) -> Response:
    k = get_karaoke_instance()
    rc = k.queue_manager.enqueue(song, user)
    broadcast_event("queue_update")
    song_title = k.song_manager.display_name_from_path(song)
    return jsonify({"song": song_title, "success": rc})


@queue_api_bp.route("/api/enqueue", methods=["POST"])
@public
@queue_api_bp.arguments(EnqueueForm, location="form")
def enqueue_form(form):
    """Add a song to the queue."""
    return _do_enqueue(form["song_to_add"], form["song_added_by"])


@queue_api_bp.route("/api/queue/downloads")
@public
def get_current_downloads():
    """Get the status of current and pending downloads."""
    k = get_karaoke_instance()
    return jsonify(k.download_manager.get_downloads_status())


@queue_api_bp.route("/api/queue/downloads/errors/<error_id>", methods=["DELETE"])
@public
def delete_download_error(error_id):
    """Remove a download error from the list."""
    k = get_karaoke_instance()
    if k.download_manager.remove_error(error_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Error not found"}), 404


@queue_api_bp.route("/api/queue/downloads/errors/<error_id>/retry", methods=["POST"])
@public
def retry_download_error(error_id):
    """Re-queue a failed download."""
    k = get_karaoke_instance()
    if k.download_manager.retry_error(error_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Error not found"}), 404
