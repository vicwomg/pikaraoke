"""API for the song queue and the downloads feeding it."""

from urllib.parse import unquote

import flask_babel
from flask import Response, jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, post_load

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import broadcast_event, get_karaoke_instance
from pikaraoke.lib.singer_manager import SINGER_NAME_MAX_LENGTH, normalize_name

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
        required=True,
        metadata={"description": "Name of the user adding the song"},
    )

    @post_load
    def clean_name(self, data, **kwargs):
        # Normalise so the queued name, the play's performer and the singer row
        # all agree on one form.
        name = normalize_name(data["song_added_by"])
        if not name:
            # MSG: Error when a song is queued without a name
            raise ValidationError(_("A name is required"), "song_added_by")
        if len(name) > SINGER_NAME_MAX_LENGTH:
            # MSG: Error when a queued name is longer than the limit.
            raise ValidationError(
                _("Name must be %(max)d characters or fewer", max=SINGER_NAME_MAX_LENGTH),
                "song_added_by",
            )
        if "<" in name or ">" in name:
            # MSG: Error when a singer name contains disallowed HTML angle brackets
            raise ValidationError(_("Invalid singer name"), "song_added_by")
        data["song_added_by"] = name
        return data


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

    The choice is saved against whoever queued the song, so their next add of it
    opens in this key.

    QueueManager emits now_playing_update itself, which Karaoke bridges to the
    socket, so this path adds no broadcast_event.
    """
    k = get_karaoke_instance()
    song = unquote(query["song"])
    semitones = query["semitones"]
    # The queue item names the singer, so read it before the write.
    user = k.queue_manager.get_song_user(song)
    success = k.queue_manager.set_song_semitones(song, semitones)
    if success:
        k.singer_manager.save_key(user, song, semitones)
    return jsonify({"success": success})


def _do_enqueue(song: str, user: str) -> Response | tuple[Response, int]:
    k = get_karaoke_instance()
    if song not in k.song_manager.songs:
        return (
            jsonify(
                {
                    "song": False,
                    # MSG: Error when trying to queue a song that is not in the library
                    "success": [False, _("Song is not in the library")],
                }
            ),
            400,
        )
    # A merged name is retired; reuse the [ok, message] shape the add-song
    # handler already shows as a danger toast.
    survivor = k.singer_manager.merged_into_name(user)
    if survivor:
        # MSG: Error when queueing under a name the host merged into another. %(name)s is the surviving name.
        message = _("This name was merged into %(name)s. Set your name to continue.", name=survivor)
        return jsonify({"song": user, "success": [False, message]})
    semitones = k.singer_manager.get_saved_key(user, song)
    rc = k.queue_manager.enqueue(song, user, semitones=semitones)
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
