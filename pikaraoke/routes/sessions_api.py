"""Admin-only API for play history: sessions, plays, singers, and CSV export."""

import csv
import io

import flask_babel
from flask import Response, jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin
from pikaraoke.lib.play_history_manager import SESSION_NAME_MAX_LENGTH

_ = flask_babel.gettext

sessions_api_bp = Blueprint("sessions_api", __name__)


@sessions_api_bp.before_request
def require_admin():
    """Gate every endpoint in this blueprint.

    The singer list is effectively the guest list for the event, so this is a
    hard requirement rather than a per-route default that a new endpoint could
    forget.
    """
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    return None


# Paging bounds shared by the list endpoints. Capped rather than left open: these
# queries run on a Pi that is transcoding video at the same time, and SQLite reads
# a negative LIMIT as no limit at all, so an unvalidated one loads the whole table.
_MAX_PAGE_SIZE = 500


def _page_size(default: int | None) -> fields.Integer:
    """A row cap that cannot be negative, zero, or unbounded."""
    return fields.Integer(
        load_default=default,
        validate=validate.Range(min=1, max=_MAX_PAGE_SIZE),
        metadata={"description": f"Rows to return (1-{_MAX_PAGE_SIZE})"},
    )


def _page_offset() -> fields.Integer:
    """A row offset that cannot be negative."""
    return fields.Integer(load_default=0, validate=validate.Range(min=0))


class PlaysQuery(Schema):
    session = fields.String(load_default=None, metadata={"description": "Session UUID filter"})
    limit = _page_size(100)
    offset = _page_offset()
    sort = fields.String(
        load_default="played_at", metadata={"description": "played_at, performer or song"}
    )
    direction = fields.String(load_default="desc", metadata={"description": "asc or desc"})


class SessionsQuery(Schema):
    limit = _page_size(50)
    offset = _page_offset()


class StartSessionForm(Schema):
    # Required: this route is the host starting a session by hand, and the name
    # is what the splash screen shows while it runs. Only the auto-start on the
    # first play, which nobody is there to name, leaves a session unnamed.
    name = fields.String(
        required=True,
        validate=validate.Length(max=SESSION_NAME_MAX_LENGTH),
        metadata={"description": "Session name"},
    )

    @validates_schema
    def check_name(self, data, **kwargs):
        if not data["name"].strip():
            # MSG: Error when starting a session without supplying a name
            raise ValidationError(_("A name is required"), "name")


class SingersQuery(Schema):
    session = fields.String(load_default=None, metadata={"description": "Session UUID filter"})
    # Defaults to no cap: the session singer panel wants everyone who sang that
    # night, which is bounded by the session. A cap passed explicitly is still
    # bounded, for the autocomplete that asks against every session on record.
    limit = _page_size(None)


class ExportQuery(Schema):
    format = fields.String(
        load_default="csv",
        validate=validate.OneOf(["csv", "txt"]),
        metadata={"description": "csv (spreadsheet) or txt (human-readable list)"},
    )


class UpdateSessionForm(Schema):
    action = fields.String(required=True, validate=validate.OneOf(["rename", "end", "activate"]))
    name = fields.String(
        load_default=None,
        validate=validate.Length(max=SESSION_NAME_MAX_LENGTH),
        metadata={"description": "New name when renaming"},
    )

    @validates_schema
    def check_name(self, data, **kwargs):
        # Whitespace-only is blank: it would be truthy everywhere the name is
        # read and render as an empty session ribbon.
        if data["action"] == "rename" and not (data["name"] or "").strip():
            # MSG: Error when renaming a session without supplying a name
            raise ValidationError(_("A name is required"), "name")


@sessions_api_bp.route("/api/history/singers")
@sessions_api_bp.arguments(SingersQuery, location="query")
def get_singers(query):
    """Performer names with play counts, most active first.

    Unscoped for the singer auto-complete; scoped to a session for its singer list.
    """
    k = get_karaoke_instance()
    return jsonify({"singers": k.play_history.get_singers(query["session"], query["limit"])})


@sessions_api_bp.route("/api/history/plays")
@sessions_api_bp.arguments(PlaysQuery, location="query")
def get_plays(query):
    """Paginated play log, newest first, optionally scoped to one session."""
    k = get_karaoke_instance()
    plays = k.play_history.get_plays(
        query["session"], query["limit"], query["offset"], query["sort"], query["direction"]
    )
    return jsonify(
        {
            "plays": plays,
            "total": k.play_history.count_plays(query["session"]),
            # Its row exists but has not been resolved yet, so the UI must not
            # render the song playing right now as skipped.
            "current_play_id": k.play_history.current_play_id,
        }
    )


@sessions_api_bp.route("/api/history/plays/<int:play_id>", methods=["DELETE"])
def delete_play(play_id):
    """Delete a single play from the log."""
    k = get_karaoke_instance()
    if not k.play_history.delete_play(play_id):
        return jsonify({"success": False, "error": _("Play not found")}), 404
    return jsonify({"success": True})


@sessions_api_bp.route("/api/history/sessions")
@sessions_api_bp.arguments(SessionsQuery, location="query")
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


@sessions_api_bp.route("/api/history/sessions", methods=["POST"])
@sessions_api_bp.arguments(StartSessionForm, location="json")
def start_session(form):
    """Start a new session, closing any that is still open."""
    k = get_karaoke_instance()
    return jsonify({"success": True, "uuid": k.play_history.start_session(form["name"])})


@sessions_api_bp.route("/api/history/sessions/<session_uuid>", methods=["PUT"])
@sessions_api_bp.arguments(UpdateSessionForm, location="json")
def update_session(form, session_uuid):
    """End, activate or rename a session. The schema rejects any other action."""
    k = get_karaoke_instance()

    if form["action"] == "end":
        k.play_history.end_session()
        return jsonify({"success": True})

    if form["action"] == "activate":
        if not k.play_history.activate_session(session_uuid):
            return jsonify({"success": False, "error": _("Session not found")}), 404
        return jsonify({"success": True})

    if not k.play_history.rename_session(session_uuid, form["name"]):
        return jsonify({"success": False, "error": _("Session not found")}), 404
    return jsonify({"success": True})


@sessions_api_bp.route("/api/history/sessions/<session_uuid>", methods=["DELETE"])
def delete_session(session_uuid):
    """Delete a session and all of its plays."""
    k = get_karaoke_instance()
    if not k.play_history.delete_session(session_uuid):
        return jsonify({"success": False, "error": _("Session not found")}), 404
    return jsonify({"success": True})


def _export_csv(session_uuid: str, plays: list[dict]) -> Response:
    """Render plays as CSV, for spreadsheets."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_("Played At"), _("Performer"), _("Song"), _("Status")])
    for play in plays:
        writer.writerow(
            [
                play["played_at"],
                play["performer"],
                play["song"],
                # The same vocabulary the play log shows on screen.
                _("Played") if play["completed"] else _("Skipped"),
            ]
        )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="pikaraoke-{session_uuid}.csv"'},
    )


def _export_txt(session_uuid: str, plays: list[dict]) -> Response:
    """Render plays as a numbered, human-readable list (the #213 request)."""
    lines = [_("PiKaraoke - Play History"), ""]
    for i, play in enumerate(plays, 1):
        # played_at is "YYYY-MM-DD HH:MM:SS"; minutes are enough for a set list.
        line = f"{i}. {play['played_at'][:16]}  {play['performer']} - {play['song']}"
        if not play["completed"]:
            line += "  " + _("(skipped)")
        lines.append(line)
    return Response(
        "\n".join(lines) + "\n",
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="pikaraoke-{session_uuid}.txt"'},
    )


@sessions_api_bp.route("/api/history/export/<session_uuid>")
@sessions_api_bp.arguments(ExportQuery, location="query")
def export_session(query, session_uuid):
    """Download a session's plays as CSV or a human-readable text list."""
    k = get_karaoke_instance()
    # Otherwise a deleted session downloads as a header row and no plays, which
    # reads as "nobody sang" rather than "that session is gone".
    if not k.play_history.session_exists(session_uuid):
        return jsonify({"success": False, "error": _("Session not found")}), 404
    plays = k.play_history.export_plays(session_uuid)
    if query["format"] == "txt":
        return _export_txt(session_uuid, plays)
    return _export_csv(session_uuid, plays)
