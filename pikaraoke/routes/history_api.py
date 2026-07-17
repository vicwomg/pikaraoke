"""Admin-only API for play history: sessions, plays, singers, and CSV export."""

import csv
import io

import flask_babel
from flask import Response, jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin

_ = flask_babel.gettext

history_api_bp = Blueprint("history_api", __name__)


@history_api_bp.before_request
def require_admin():
    """Gate every endpoint in this blueprint.

    The singer list is effectively the guest list for the event, so this is a
    hard requirement rather than a per-route default that a new endpoint could
    forget.
    """
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    return None


class PlaysQuery(Schema):
    session = fields.String(load_default=None, metadata={"description": "Session UUID filter"})
    limit = fields.Integer(load_default=100)
    offset = fields.Integer(load_default=0)
    sort = fields.String(
        load_default="played_at", metadata={"description": "played_at, performer or song"}
    )
    direction = fields.String(load_default="desc", metadata={"description": "asc or desc"})


class SessionsQuery(Schema):
    limit = fields.Integer(load_default=50)
    offset = fields.Integer(load_default=0)


class StartSessionForm(Schema):
    name = fields.String(load_default=None, metadata={"description": "Optional session name"})


class SingersQuery(Schema):
    session = fields.String(load_default=None, metadata={"description": "Session UUID filter"})


class UpdateSessionForm(Schema):
    action = fields.String(required=True, metadata={"description": "'rename', 'end' or 'activate'"})
    name = fields.String(load_default=None, metadata={"description": "New name when renaming"})


def _with_titles(plays: list[dict]) -> list[dict]:
    """Attach a display title to each play, or None when the song file is gone."""
    k = get_karaoke_instance()
    for play in plays:
        file_path = play.get("file_path")
        play["song"] = k.song_manager.display_name_from_path(file_path) if file_path else None
    return plays


@history_api_bp.route("/api/history/singers")
@history_api_bp.arguments(SingersQuery, location="query")
def get_singers(query):
    """Performer names with play counts, most active first.

    Unscoped for the KJ auto-complete; scoped to a session for its singer list.
    """
    k = get_karaoke_instance()
    return jsonify({"singers": k.play_history.get_singers(query["session"])})


@history_api_bp.route("/api/history/plays")
@history_api_bp.arguments(PlaysQuery, location="query")
def get_plays(query):
    """Paginated play log, newest first, optionally scoped to one session."""
    k = get_karaoke_instance()
    plays = k.play_history.get_plays(
        query["session"], query["limit"], query["offset"], query["sort"], query["direction"]
    )
    return jsonify(
        {
            "plays": _with_titles(plays),
            "total": k.play_history.count_plays(query["session"]),
            # Its row exists but has not been resolved yet, so the UI must not
            # render the song playing right now as skipped.
            "current_play_id": k.play_history.current_play_id,
        }
    )


@history_api_bp.route("/api/history/plays/<int:play_id>", methods=["DELETE"])
def delete_play(play_id):
    """Delete a single play from the log."""
    k = get_karaoke_instance()
    if not k.play_history.delete_play(play_id):
        return jsonify({"success": False, "error": _("Play not found")}), 404
    return jsonify({"success": True})


@history_api_bp.route("/api/history/sessions")
@history_api_bp.arguments(SessionsQuery, location="query")
def get_sessions(query):
    """Session list with play counts, plus the currently active session."""
    k = get_karaoke_instance()
    return jsonify(
        {
            "sessions": k.play_history.get_sessions(query["limit"], query["offset"]),
            # Without this a caller cannot tell a full list from a truncated
            # page, which is how older sessions went missing silently.
            "total": k.play_history.count_sessions(),
            "current": k.play_history.get_current_session(),
        }
    )


@history_api_bp.route("/api/history/sessions", methods=["POST"])
@history_api_bp.arguments(StartSessionForm, location="json")
def start_session(form):
    """Start a new session, closing any that is still open."""
    k = get_karaoke_instance()
    return jsonify({"success": True, "uuid": k.play_history.start_session(form["name"])})


@history_api_bp.route("/api/history/sessions/<session_uuid>", methods=["PUT"])
@history_api_bp.arguments(UpdateSessionForm, location="json")
def update_session(form, session_uuid):
    """End or rename a session."""
    k = get_karaoke_instance()

    if form["action"] == "end":
        k.play_history.end_session()
        return jsonify({"success": True})

    if form["action"] == "activate":
        if not k.play_history.activate_session(session_uuid):
            return jsonify({"success": False, "error": _("Session not found")}), 404
        return jsonify({"success": True})

    if form["action"] == "rename":
        if not form["name"]:
            return jsonify({"success": False, "error": _("A name is required")}), 400
        if not k.play_history.rename_session(session_uuid, form["name"]):
            return jsonify({"success": False, "error": _("Session not found")}), 404
        return jsonify({"success": True})

    return jsonify({"success": False, "error": _("Unknown action")}), 400


@history_api_bp.route("/api/history/sessions/<session_uuid>", methods=["DELETE"])
def delete_session(session_uuid):
    """Delete a session and all of its plays."""
    k = get_karaoke_instance()
    if not k.play_history.delete_session(session_uuid):
        return jsonify({"success": False, "error": _("Session not found")}), 404
    return jsonify({"success": True})


@history_api_bp.route("/api/history/export/<session_uuid>")
def export_session(session_uuid):
    """Download a session's plays as CSV."""
    k = get_karaoke_instance()
    plays = _with_titles(k.play_history.export_plays(session_uuid))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Played At", "Performer", "Song", "Status"])
    for play in plays:
        writer.writerow(
            [
                play["played_at"],
                play["performer"],
                play["song"] or _("(song removed from library)"),
                "Played" if play["completed"] else "Skipped",
            ]
        )

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="pikaraoke-{session_uuid}.csv"'},
    )
