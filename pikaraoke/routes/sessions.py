"""Admin-only play history and rankings pages."""

import flask_babel
from flask import flash, redirect, render_template, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

sessions_bp = Blueprint("sessions", __name__)

# Row counts offered by the "Show" dropdowns on the rankings page.
_RANKING_SIZES = [10, 20, 50, 100]


class RankingsQuery(Schema):
    """How many rows to show in each rankings list. These are top-N lists, so a
    row-count selector stands in for pagination."""

    songs = fields.Integer(load_default=20, validate=validate.OneOf(_RANKING_SIZES))
    performers = fields.Integer(load_default=20, validate=validate.OneOf(_RANKING_SIZES))
    sessions = fields.Integer(load_default=10, validate=validate.OneOf(_RANKING_SIZES))


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
    )


@sessions_bp.route("/rankings")
@sessions_bp.arguments(RankingsQuery, location="query")
def rankings(query):
    """Most-played songs, most active performers, and busiest sessions."""
    k = get_karaoke_instance()
    return render_template(
        "rankings.html",
        site_title=get_site_name(),
        title="Rankings",
        top_songs=k.play_history.get_top_songs(query["songs"]),
        top_performers=k.play_history.get_singers(limit=query["performers"]),
        sessions=k.play_history.get_sessions(limit=query["sessions"]),
        limits=query,
        ranking_sizes=_RANKING_SIZES,
    )
