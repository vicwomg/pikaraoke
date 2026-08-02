"""Play history pages: session management, the play log, and rankings."""

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.play_history_manager import SESSION_NAME_MAX_LENGTH

_ = flask_babel.gettext

sessions_bp = Blueprint("sessions", __name__)

# What the room came for: the log a guest looks up to queue something they sang
# last time, and the charts everyone wants to see. Managing sessions stays with
# the host. Deny by default, with an allowlist, so a page added here is
# host-only until someone decides otherwise.
_PUBLIC_ENDPOINTS = {"sessions.history", "sessions.rankings"}

# Every paged table in this feature uses one page size, so the pagers all read
# the same and none of them needs a control of its own.
PAGE_SIZE = 50

# How many sessions the filter dropdowns offer. They are dropdowns, and nobody
# scrolls one back past a few months of nights.
_FILTER_SESSIONS = 200

# Row counts offered by the "Show" dropdowns on the rankings page.
_RANKING_SIZES = [10, 20, 50, 100]


class RankingsQuery(Schema):
    """What the rankings cover, and how many rows of each to show. The lists are
    top-N, so a row-count selector stands in for pagination."""

    songs = fields.Integer(load_default=20, validate=validate.OneOf(_RANKING_SIZES))
    performers = fields.Integer(load_default=20, validate=validate.OneOf(_RANKING_SIZES))
    session = fields.String(load_default="", metadata={"description": "Session UUID filter"})


class HistoryQuery(Schema):
    session = fields.String(load_default="", metadata={"description": "Session UUID filter"})


@sessions_bp.before_request
def require_admin():
    """Gate every page in this blueprint that is not named in _PUBLIC_ENDPOINTS."""
    if request.endpoint in _PUBLIC_ENDPOINTS or is_admin():
        return None
    # MSG: Message shown when a non-admin tries to open a host-only history page
    flash(_("You don't have permission to view this page"), "is-danger")
    return redirect(url_for("home.home"))


def _filter_sessions() -> list[dict]:
    """The sessions the filter dropdowns offer, newest first."""
    return get_karaoke_instance().play_history.get_sessions(limit=_FILTER_SESSIONS)


@sessions_bp.route("/sessions")
def sessions():
    """Session management: the night in progress, and every night on record."""
    return render_template(
        "sessions.html",
        site_title=get_site_name(),
        title="Sessions",
        page_size=PAGE_SIZE,
        # The API rejects anything longer, so the page enforces the same cap
        # rather than letting the host type a name that is refused on submit.
        session_name_max_length=SESSION_NAME_MAX_LENGTH,
    )


@sessions_bp.route("/history")
@sessions_bp.arguments(HistoryQuery, location="query")
def history(query):
    """The play log, showing every session or one, for anyone in the room."""
    return render_template(
        "history.html",
        site_title=get_site_name(),
        title="Play History",
        page_size=PAGE_SIZE,
        # Deleting an entry is a host action; queuing a song back up is not.
        admin=is_admin(),
        sessions=_filter_sessions(),
        selected_session=query["session"],
    )


@sessions_bp.route("/rankings")
@sessions_bp.arguments(RankingsQuery, location="query")
def rankings(query):
    """Most-played songs and most active performers, all time or by session."""
    k = get_karaoke_instance()
    session_uuid = query["session"] or None
    return render_template(
        "rankings.html",
        site_title=get_site_name(),
        title="Rankings",
        top_songs=k.play_history.get_top_songs(query["songs"], session_uuid),
        top_performers=k.play_history.get_singers(
            session_uuid, limit=query["performers"], completed_only=True
        ),
        sessions=_filter_sessions(),
        selected_session=query["session"],
        limits=query,
        ranking_sizes=_RANKING_SIZES,
    )
