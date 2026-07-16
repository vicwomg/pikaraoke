"""Admin-only play history and rankings pages."""

import flask_babel
from flask import flash, redirect, render_template, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

history_bp = Blueprint("history", __name__)


@history_bp.before_request
def require_admin():
    """Play history is a KJ management tool; guests never see it."""
    if not is_admin():
        # MSG: Message shown when a non-admin tries to open the play history pages
        flash(_("You don't have permission to view play history"), "is-danger")
        return redirect(url_for("home.home"))
    return None


@history_bp.route("/history")
def history():
    """Play log page with session management."""
    return render_template(
        "history.html",
        site_title=get_site_name(),
        title="History",
        admin=True,
    )


@history_bp.route("/rankings")
def rankings():
    """Most-played songs, most active performers, and busiest sessions."""
    k = get_karaoke_instance()
    return render_template(
        "rankings.html",
        site_title=get_site_name(),
        title="Rankings",
        admin=True,
        top_songs=k.play_history.get_top_songs(),
        top_performers=k.play_history.get_singers()[:20],
        sessions=k.play_history.get_sessions(limit=10),
    )
