"""History routes for viewing play history."""

from __future__ import annotations

from urllib.parse import unquote

import flask_babel
from flask import Blueprint, flash, jsonify, render_template, request, url_for
from flask_paginate import Pagination, get_page_parameter

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

history_bp = Blueprint("history", __name__)


@history_bp.route("/history/play/<int:play_id>", methods=["DELETE"])
def delete_play(play_id: int):
    """Delete a play record from history.

    Users can delete their own plays. Admins can delete any play.

    Args:
        play_id: The play record ID.

    Query params:
        user: The requesting user's name (for permission check).
    """
    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    db = k.db
    play = db.get_play(play_id)
    if not play:
        return jsonify({"success": False, "message": _("Play not found")}), 404

    # Check permission
    admin = is_admin()
    username = request.args.get("user", "").strip()

    if not admin:
        if not username:
            return jsonify({"success": False, "message": _("User identification required")}), 400

        if not db.can_user_delete_play(play_id, username):
            return jsonify({"success": False, "message": _("Permission denied")}), 403

    # Delete the play
    success = db.delete_play(play_id)
    if success:
        return jsonify({"success": True, "message": _("Play deleted")})
    else:
        return jsonify({"success": False, "message": _("Failed to delete play")}), 500


@history_bp.route("/history/api/session-dates")
def get_session_dates():
    """Get dates that have sessions with session names.

    Returns JSON with dates and their session info for calendar highlighting.
    """
    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "dates": {}})

    db = k.db

    try:
        # Get all sessions with their dates
        sessions = db.get_sessions_with_names()
        dates_data = {}

        for session in sessions:
            if session.get("started_at"):
                date_str = session["started_at"][:10]  # Extract YYYY-MM-DD
                if date_str not in dates_data:
                    dates_data[date_str] = []
                dates_data[date_str].append(session["name"])

        return jsonify({"success": True, "dates": dates_data})
    except Exception:
        return jsonify({"success": False, "dates": {}})


@history_bp.route("/history/performers")
def performers():
    """View all performers with play counts and aliases.

    Query parameters:
        page: Page number (default 1)
        limit: Results per page (default 20)
        date_from: Start date filter (YYYY-MM-DD)
        date_to: End date filter (YYYY-MM-DD)
        sort_by: Column to sort by (canonical_name, play_count)
        sort_order: ASC or DESC (default DESC)
    """
    k = get_karaoke_instance()
    site_name = get_site_name()

    if not k.db:
        flash(_("Play history database is not available"), "is-danger")
        return render_template(
            "performers.html",
            site_title=site_name,
            title=_("Performers"),
            performers=[],
            pagination=None,
            admin=is_admin(),
        )

    db = k.db

    # Get filter parameters
    page = request.args.get(get_page_parameter(), type=int, default=1)
    limit = request.args.get("limit", type=int, default=20)
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    sort_by = request.args.get("sort_by", "play_count")
    sort_order = request.args.get("sort_order", "DESC")

    offset = (page - 1) * limit

    try:
        performers_list = db.get_performers(
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total_count = db.get_performers_count(date_from=date_from, date_to=date_to)
    except Exception as e:
        flash(_("Error loading performers: %s") % str(e), "is-danger")
        performers_list = []
        total_count = 0

    # Build pagination URL
    args = request.args.copy()
    args.pop("_", None)
    page_param = get_page_parameter()
    args[page_param] = "{0}"
    args_dict = args.to_dict()
    pagination_href = unquote(url_for("history.performers", **args_dict))

    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=total_count,
        search=False,
        record_name="performers",
        per_page=limit,
        display_msg=_("Showing <b>{start} - {end}</b> of <b>{total}</b> performers"),
        href=pagination_href,
    )

    return render_template(
        "performers.html",
        site_title=site_name,
        title=_("Performers"),
        performers=performers_list,
        pagination=pagination,
        total_count=total_count,
        date_from=date_from or "",
        date_to=date_to or "",
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        admin=is_admin(),
    )


@history_bp.route("/history/rankings")
def rankings():
    """View rankings (top users, songs, sessions, days).

    Query parameters:
        date_from: Start date filter (YYYY-MM-DD)
        date_to: End date filter (YYYY-MM-DD)
        session_id: Filter by specific session
        sessions_count: Filter by last N sessions
        limit: Number of results per ranking (default 10)
    """
    k = get_karaoke_instance()
    site_name = get_site_name()

    if not k.db:
        flash(_("Play history database is not available"), "is-danger")
        return render_template(
            "rankings.html",
            site_title=site_name,
            title=_("Rankings"),
            top_users=[],
            top_songs=[],
            busiest_sessions=[],
            busiest_days=[],
            admin=is_admin(),
        )

    db = k.db

    # Get filter parameters
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    session_id = request.args.get("session_id")
    sessions_count = request.args.get("sessions_count", type=int)
    limit = request.args.get("limit", type=int, default=10)

    # Build session_ids list
    session_ids = None
    if session_id:
        session_ids = [session_id]
    elif sessions_count and sessions_count > 0:
        session_ids = db.get_session_ids_last_n(sessions_count)

    try:
        top_users = db.get_top_users(limit, date_from, date_to, session_ids)
        top_songs = db.get_top_songs(limit, date_from, date_to, session_ids)
        busiest_sessions = db.get_busiest_sessions(limit, date_from, date_to)
        busiest_days = db.get_busiest_days(limit, date_from, date_to)
        sessions = db.get_sessions_with_names(date_from=date_from, date_to=date_to)
    except Exception as e:
        flash(_("Error loading rankings: %s") % str(e), "is-danger")
        top_users = []
        top_songs = []
        busiest_sessions = []
        busiest_days = []
        sessions = []

    return render_template(
        "rankings.html",
        site_title=site_name,
        title=_("Rankings"),
        top_users=top_users,
        top_songs=top_songs,
        busiest_sessions=busiest_sessions,
        busiest_days=busiest_days,
        sessions=sessions,
        date_from=date_from or "",
        date_to=date_to or "",
        session_id=session_id or "",
        sessions_count=sessions_count or "",
        limit=limit,
        admin=is_admin(),
    )


@history_bp.route("/history")
def history():
    """View play history with filtering and pagination.

    Query parameters:
        page: Page number (default 1)
        limit: Results per page (default 20)
        date_from: Start date filter (YYYY-MM-DD)
        date_to: End date filter (YYYY-MM-DD)
        session_id: Filter by specific session
        sessions_count: Filter by last N sessions
        user: Filter by user (canonical name)
        alias: Filter to specific display_name (requires user)
        sort_by: Column to sort by (timestamp, song, canonical_name)
        sort_order: ASC or DESC (default DESC)
    """
    k = get_karaoke_instance()
    site_name = get_site_name()

    if not k.db:
        flash(_("Play history database is not available"), "is-danger")
        return render_template(
            "history.html",
            site_title=site_name,
            title=_("History"),
            plays=[],
            pagination=None,
            sessions=[],
            distinct_users=[],
            admin=is_admin(),
        )

    db = k.db

    # Get filter parameters from URL
    page = request.args.get(get_page_parameter(), type=int, default=1)
    limit = request.args.get("limit", type=int, default=20)
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    session_id = request.args.get("session_id")
    sessions_count = request.args.get("sessions_count", type=int)
    user_filter = request.args.get("user")
    alias_filter = request.args.get("alias")
    song_filter = request.args.get("song")
    sort_by = request.args.get("sort_by", "timestamp")
    sort_order = request.args.get("sort_order", "DESC")

    # Build session_ids list from either session_id or sessions_count
    session_ids = None
    if session_id:
        session_ids = [session_id]
    elif sessions_count and sessions_count > 0:
        session_ids = db.get_session_ids_last_n(sessions_count)

    # Calculate offset from page
    offset = (page - 1) * limit

    try:
        # Get plays with filters
        plays = db.get_last_plays(
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            session_ids=session_ids,
            user_filter=user_filter,
            alias_filter=alias_filter,
            song_filter=song_filter,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Get total count for pagination
        total_count = db.get_plays_count(
            date_from=date_from,
            date_to=date_to,
            session_ids=session_ids,
            user_filter=user_filter,
            alias_filter=alias_filter,
            song_filter=song_filter,
        )

        # Get sessions for dropdown
        sessions = db.get_sessions_with_names(date_from=date_from, date_to=date_to)

        # Get distinct users for autocomplete
        distinct_users = db.get_distinct_users()

    except Exception as e:
        flash(_("Error loading history: %s") % str(e), "is-danger")
        plays = []
        total_count = 0
        sessions = []
        distinct_users = []

    # Build pagination URL with current filters preserved
    args = request.args.copy()
    args.pop("_", None)  # Remove cache buster if present
    page_param = get_page_parameter()
    args[page_param] = "{0}"
    args_dict = args.to_dict()
    pagination_href = unquote(url_for("history.history", **args_dict))

    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=total_count,
        search=False,
        record_name="plays",
        per_page=limit,
        display_msg=_("Showing <b>{start} - {end}</b> of <b>{total}</b> plays"),
        href=pagination_href,
    )

    return render_template(
        "history.html",
        site_title=site_name,
        title=_("History"),
        plays=plays,
        pagination=pagination,
        sessions=sessions,
        distinct_users=distinct_users,
        total_count=total_count,
        # Current filter values for form state
        date_from=date_from or "",
        date_to=date_to or "",
        session_id=session_id or "",
        sessions_count=sessions_count or "",
        user_filter=user_filter or "",
        alias_filter=alias_filter or "",
        song_filter=song_filter or "",
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        admin=is_admin(),
    )
