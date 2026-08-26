"""Play history pages: session management, the play log, and rankings."""

import flask_babel
from flask import render_template
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate

from pikaraoke.lib.auth import host_only, public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.play_history_manager import SESSION_NAME_MAX_LENGTH

_ = flask_babel.gettext
_lazy = flask_babel.lazy_gettext

sessions_bp = Blueprint("sessions", __name__)

# Sized to the table rather than shared, because the two differ by roughly the
# number of songs in a night: sessions gain a row per night and total in the
# tens, plays gain one per song and total in the thousands. One number cannot
# serve both -- large enough for plays leaves the sessions pager permanently
# inert, small enough for sessions turns a year of plays into hundreds of pages.
# Neither page offers a control, so these are the only sizes there are.
SESSIONS_PAGE_SIZE = 20
PLAYS_PAGE_SIZE = 50

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
    performer = fields.String(load_default="", metadata={"description": "Performer name filter"})
    # Likewise reached from a song title, in the log itself or on the rankings.
    # youtube_id rides along so the filter selects the same plays the chart row
    # counted; the title is what the chip shows.
    song = fields.String(load_default="", metadata={"description": "Song title filter"})
    youtube_id = fields.String(load_default="", metadata={"description": "Song's YouTube id"})


def _filter_sessions() -> list[dict]:
    """The sessions the filter dropdowns offer, newest first."""
    return get_karaoke_instance().play_history.get_sessions(limit=_FILTER_SESSIONS)


@sessions_bp.route("/sessions")
# MSG: Message shown when a non-admin tries to open a host-only history page
@host_only(_lazy("You don't have permission to view this page"))
def sessions():
    """Session management: the night in progress, and every night on record."""
    return render_template(
        "sessions.html",
        site_title=get_site_name(),
        # MSG: Title of the session management page.
        title=_("Sessions"),
        page_size=SESSIONS_PAGE_SIZE,
        # The API rejects anything longer, so the page enforces the same cap
        # rather than letting the host type a name that is refused on submit.
        session_name_max_length=SESSION_NAME_MAX_LENGTH,
    )


@sessions_bp.route("/history")
@public
@sessions_bp.arguments(HistoryQuery, location="query")
def history(query):
    """The play log, showing every session or one, for anyone in the room."""
    return render_template(
        "history.html",
        site_title=get_site_name(),
        # MSG: Title of the page logging everything that has been sung.
        title=_("Play History"),
        page_size=PLAYS_PAGE_SIZE,
        # Deleting an entry is a host action; queuing a song back up is not.
        admin=is_admin(),
        sessions=_filter_sessions(),
        selected_session=query["session"],
        selected_performer=query["performer"],
        selected_song=query["song"],
        selected_youtube_id=query["youtube_id"],
    )


@sessions_bp.route("/rankings")
@public
@sessions_bp.arguments(RankingsQuery, location="query")
def rankings(query):
    """Most-played songs and most active performers, all time or by session."""
    k = get_karaoke_instance()
    session_uuid = query["session"] or None
    return render_template(
        "rankings.html",
        site_title=get_site_name(),
        # MSG: Title of the most-played songs and most active singers page.
        title=_("Rankings"),
        top_songs=k.play_history.get_top_songs(query["songs"], session_uuid),
        top_performers=k.play_history.get_singers(
            session_uuid, limit=query["performers"], completed_only=True
        ),
        sessions=_filter_sessions(),
        selected_session=query["session"],
        limits=query,
        ranking_sizes=_RANKING_SIZES,
    )
