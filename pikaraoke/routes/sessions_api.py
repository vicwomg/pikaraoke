"""Admin-only API for play history: sessions, plays, singers, and CSV export."""

import csv
import io

import flask_babel
from flask import Response, jsonify, request
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin
from pikaraoke.lib.play_history_manager import RESET_SCOPES, SESSION_NAME_MAX_LENGTH

_ = flask_babel.gettext

sessions_api_bp = Blueprint("sessions_api", __name__)

# The play log is the one thing the whole room may read: the Play History page
# is open to every device, so guests can see what has been sung and queue it up
# again. Everything else here -- the singer directory, which is effectively the
# guest list, and every mutation -- stays with the host.
_PUBLIC_ENDPOINTS = {"sessions_api.get_plays"}


@sessions_api_bp.before_request
def require_admin():
    """Gate every endpoint in this blueprint that is not named above.

    Deny by default, with an allowlist, rather than a check per route: a new
    endpoint added here is host-only until someone decides otherwise, instead of
    being public because a decorator was forgotten.
    """
    if request.endpoint in _PUBLIC_ENDPOINTS or is_admin():
        return None
    return jsonify({"error": "Unauthorized"}), 403


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
    include_skipped = fields.Boolean(
        load_default=True,
        metadata={"description": "Show songs nobody sang through"},
    )


class SessionsQuery(Schema):
    limit = _page_size(50)
    offset = _page_offset()
    include_unnamed = fields.Boolean(
        load_default=True,
        metadata={"description": "Include auto-started sessions the host never named"},
    )


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
    completed_only = fields.Boolean(
        load_default=False,
        metadata={"description": "Count only songs sung through, for the singer lists"},
    )


class ResetHistoryQuery(Schema):
    """What a reset is allowed to destroy.

    scope is required rather than defaulted: a parameter that goes missing must
    fail the request, never fall through to the wider of two irreversible
    deletes.
    """

    scope = fields.String(required=True, validate=validate.OneOf(RESET_SCOPES))
    session = fields.String(load_default=None, metadata={"description": "UUID when scope=session"})

    @validates_schema
    def check_session(self, data, **kwargs):
        if data["scope"] == "session" and not data["session"]:
            # MSG: Error when resetting one session's plays without saying which session
            raise ValidationError(_("A session is required"), "session")


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

    Unscoped for the singer auto-complete; scoped to a session for its singer
    list. Only the auto-complete leaves completed_only off: it is a directory of
    who sings here, so dropping someone who skipped their only song would just
    mean the host typing the name out again.
    """
    k = get_karaoke_instance()
    singers = k.play_history.get_singers(query["session"], query["limit"], query["completed_only"])
    return jsonify({"singers": singers})


@sessions_api_bp.route("/api/history/plays")
@sessions_api_bp.arguments(PlaysQuery, location="query")
def get_plays(query):
    """Paginated play log, newest first, optionally scoped to one session."""
    k = get_karaoke_instance()
    skipped = query["include_skipped"]
    plays = k.play_history.get_plays(
        query["session"],
        query["limit"],
        query["offset"],
        query["sort"],
        query["direction"],
        skipped,
    )
    total = k.play_history.count_plays(query["session"], skipped)
    return jsonify({"plays": plays, "total": total})


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
    unnamed = query["include_unnamed"]
    return jsonify(
        {
            "sessions": k.play_history.get_sessions(query["limit"], query["offset"], unnamed),
            # Without this a caller cannot tell a full list from a truncated
            # page, which is how older sessions went missing silently.
            "total": k.play_history.count_sessions(unnamed),
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


@sessions_api_bp.route("/api/history", methods=["DELETE"])
@sessions_api_bp.arguments(ResetHistoryQuery, location="query")
def reset_history(query):
    """Erase play history: one session's plays, or everything on record.

    Gated here as well as by the blueprint. The Play History page is already
    open to the whole room, so the blueprint's allowlist is the only thing
    standing between a guest and this; an irreversible delete does not rest on
    one line in a set literal.
    """
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403

    k = get_karaoke_instance()
    if query["scope"] == "all":
        k.play_history.clear_all_history()
        return jsonify({"success": True})

    if not k.play_history.clear_session_plays(query["session"]):
        return jsonify({"success": False, "error": _("Session not found")}), 404
    return jsonify({"success": True})


@sessions_api_bp.route("/api/history/sessions/<session_uuid>", methods=["DELETE"])
def delete_session(session_uuid):
    """Delete a session and all of its plays."""
    k = get_karaoke_instance()
    if not k.play_history.delete_session(session_uuid):
        return jsonify({"success": False, "error": _("Session not found")}), 404
    return jsonify({"success": True})


# Excel and LibreOffice read a cell starting with any of these as a formula.
# Performer names and song titles reach the log from the queue, which any device
# on the network can post to, so neither can be trusted into a spreadsheet.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str | None) -> str:
    """Stop a spreadsheet reading a queued name as a formula.

    The leading apostrophe is the spreadsheet convention for "this is text";
    it is consumed on import rather than shown in the cell.
    """
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _export_csv(session_uuid: str, plays: list[dict]) -> Response:
    """Render plays as CSV, for spreadsheets."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # No status column: the export carries performances only, so it would read
    # "Played" the whole way down.
    writer.writerow([_("Played At"), _("Performer"), _("Song")])
    for play in plays:
        writer.writerow([play["played_at"], _csv_safe(play["performer"]), _csv_safe(play["song"])])
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
        lines.append(f"{i}. {play['played_at'][:16]}  {play['performer']} - {play['song']}")
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
