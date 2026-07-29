"""Admin-only play history and rankings pages."""

import flask_babel
from flask import flash, redirect, render_template, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.play_history_manager import RANKING_SCOPES, SESSION_NAME_MAX_LENGTH

_ = flask_babel.gettext

sessions_bp = Blueprint("sessions", __name__)

# Row counts offered by the "Show" dropdowns on the rankings page.
_RANKING_SIZES = [10, 20, 50, 100]


class RankingsQuery(Schema):
    """What the rankings cover, and how many rows of each to show. The lists are
    top-N, so a row-count selector stands in for pagination."""

    songs = fields.Integer(load_default=20, validate=validate.OneOf(_RANKING_SIZES))
    performers = fields.Integer(load_default=20, validate=validate.OneOf(_RANKING_SIZES))
    scope = fields.String(load_default="all", validate=validate.OneOf(RANKING_SCOPES))


@sessions_bp.before_request
def require_admin():
    """The play log and rankings are host reporting pages; guests never see them."""
    if not is_admin():
        # MSG: Message shown when a non-admin tries to open the play history pages
        flash(_("You don't have permission to view play history"), "is-danger")
        return redirect(url_for("home.home"))
    return None


@sessions_bp.route("/sessions")
def sessions():
    """Play log page with session management."""
    return render_template(
        "sessions.html",
        site_title=get_site_name(),
        title="Sessions",
        # The API rejects anything longer, so the page enforces the same cap
        # rather than letting the host type a name that is refused on submit.
        session_name_max_length=SESSION_NAME_MAX_LENGTH,
    )


@sessions_bp.route("/rankings")
@sessions_bp.arguments(RankingsQuery, location="query")
def rankings(query):
    """Most-played songs and most active performers, all time or this session."""
    k = get_karaoke_instance()
    # Resolved in both scopes: the scope control labels itself "this session",
    # "last session" or nothing at all, before the host has clicked it.
    session = k.play_history.get_latest_session()
    session_uuid = session["uuid"] if session and query["scope"] == "session" else None
    return render_template(
        "rankings.html",
        site_title=get_site_name(),
        title="Rankings",
        top_songs=k.play_history.get_top_songs(query["songs"], session_uuid),
        top_performers=k.play_history.get_singers(
            session_uuid, limit=query["performers"], completed_only=True
        ),
        scope=query["scope"],
        session=session,
        limits=query,
        ranking_sizes=_RANKING_SIZES,
    )
