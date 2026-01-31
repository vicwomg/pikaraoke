"""Admin routes for managing play history."""

from __future__ import annotations

import flask_babel
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

admin_history_bp = Blueprint("admin_history", __name__)


@admin_history_bp.route("/admin/history")
def admin_history():
    """Admin page for managing play history.

    Requires admin authentication.
    """
    if not is_admin():
        flash(_("Admin access required"), "is-danger")
        return redirect(url_for("history.history"))

    k = get_karaoke_instance()
    site_name = get_site_name()

    if not k.db:
        flash(_("Play history database is not available"), "is-danger")
        return render_template(
            "admin_history.html",
            site_title=site_name,
            title=_("Manage History"),
            sessions=[],
            aliases=[],
            distinct_users=[],
            admin=True,
        )

    db = k.db
    sessions = db.get_sessions_with_names()
    aliases = db.get_all_aliases()
    distinct_users = db.get_distinct_users()

    return render_template(
        "admin_history.html",
        site_title=site_name,
        title=_("Manage History"),
        sessions=sessions,
        aliases=aliases,
        distinct_users=distinct_users,
        admin=True,
    )


@admin_history_bp.route("/admin/history/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str):
    """Get session details.

    Args:
        session_id: The session UUID.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    session = k.db.get_session(session_id)
    if not session:
        return jsonify({"success": False, "message": _("Session not found")}), 404

    return jsonify({"success": True, "session": session})


@admin_history_bp.route("/admin/history/sessions/<session_id>/name", methods=["POST"])
def update_session_name(session_id: str):
    """Update a session's display name.

    Args:
        session_id: The session UUID.

    Form data:
        name: The new session name.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": _("Session name cannot be empty")}), 400

    success = k.db.update_session_name(session_id, name)
    if success:
        flash(_("Session name updated"), "is-success")
        return jsonify({"success": True, "message": _("Session name updated")})
    else:
        return jsonify({"success": False, "message": _("Session not found")}), 404


@admin_history_bp.route("/admin/history/sessions/merge", methods=["POST"])
def merge_sessions():
    """Merge multiple sessions into one.

    Form data:
        source_sessions: Comma-separated list of source session IDs.
        target_session: The target session ID.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    source_sessions = request.form.get("source_sessions", "").strip()
    target_session = request.form.get("target_session", "").strip()

    if not source_sessions or not target_session:
        return jsonify({"success": False, "message": _("Missing required parameters")}), 400

    source_ids = [s.strip() for s in source_sessions.split(",") if s.strip()]
    if not source_ids:
        return jsonify({"success": False, "message": _("No source sessions specified")}), 400

    plays_moved = k.db.merge_sessions(source_ids, target_session)
    flash(_("Merged %d plays into target session") % plays_moved, "is-success")
    return jsonify({
        "success": True,
        "message": _("Merged %d plays") % plays_moved,
        "plays_moved": plays_moved,
    })


@admin_history_bp.route("/admin/history/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    """Delete a session and all its history.

    Args:
        session_id: The session UUID.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    plays_deleted, sessions_deleted = k.db.delete_session(session_id)
    if sessions_deleted > 0:
        flash(_("Session deleted with %d plays") % plays_deleted, "is-warning")
        return jsonify({
            "success": True,
            "message": _("Session deleted"),
            "plays_deleted": plays_deleted,
        })
    else:
        return jsonify({"success": False, "message": _("Session not found")}), 404


# --- User Alias Management (Phase 4) ---


@admin_history_bp.route("/admin/history/aliases", methods=["GET"])
def get_aliases():
    """Get all user aliases."""
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    aliases = k.db.get_all_aliases()
    return jsonify({"success": True, "aliases": aliases})


@admin_history_bp.route("/admin/history/aliases", methods=["POST"])
def add_alias():
    """Add a new user alias.

    Form data:
        alias: The alias name.
        canonical_name: The canonical user name to map to.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    alias = request.form.get("alias", "").strip()
    canonical_name = request.form.get("canonical_name", "").strip()

    if not alias or not canonical_name:
        return jsonify({"success": False, "message": _("Alias and canonical name required")}), 400

    if alias == canonical_name:
        return jsonify({"success": False, "message": _("Alias cannot be same as canonical name")}), 400

    success = k.db.add_alias(alias, canonical_name)
    if success:
        flash(_("Alias added: %s -> %s") % (alias, canonical_name), "is-success")
        return jsonify({"success": True, "message": _("Alias added")})
    else:
        return jsonify({"success": False, "message": _("Alias already exists")}), 409


@admin_history_bp.route("/admin/history/aliases/<path:alias>", methods=["DELETE"])
def remove_alias(alias: str):
    """Remove a user alias.

    Args:
        alias: The alias to remove (URL-encoded).
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    success = k.db.remove_alias(alias)
    if success:
        flash(_("Alias removed: %s") % alias, "is-warning")
        return jsonify({"success": True, "message": _("Alias removed")})
    else:
        return jsonify({"success": False, "message": _("Alias not found")}), 404


@admin_history_bp.route("/admin/history/aliases/<path:alias>", methods=["PUT"])
def update_alias(alias: str):
    """Update a user alias mapping.

    Args:
        alias: The alias to update (URL-encoded).

    Form data:
        canonical_name: The new canonical user name.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    canonical_name = request.form.get("canonical_name", "").strip()
    if not canonical_name:
        return jsonify({"success": False, "message": _("Canonical name required")}), 400

    success = k.db.update_alias_canonical_user(alias, canonical_name)
    if success:
        flash(_("Alias updated: %s -> %s") % (alias, canonical_name), "is-success")
        return jsonify({"success": True, "message": _("Alias updated")})
    else:
        return jsonify({"success": False, "message": _("Alias not found")}), 404


# --- Admin Play Editing (Phase 8) ---


@admin_history_bp.route("/admin/history/plays/<int:play_id>", methods=["GET"])
def get_play(play_id: int):
    """Get a play record for editing.

    Args:
        play_id: The play record ID.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    play = k.db.get_play(play_id)
    if not play:
        return jsonify({"success": False, "message": _("Play not found")}), 404

    return jsonify({"success": True, "play": play})


@admin_history_bp.route("/admin/history/plays/<int:play_id>/user", methods=["POST"])
def update_play_user(play_id: int):
    """Update a play's user/display name (admin only).

    Args:
        play_id: The play record ID.

    Form data:
        display_name: New display name for the play.
        canonical_name: Optional canonical user name.
    """
    if not is_admin():
        return jsonify({"success": False, "message": _("Admin access required")}), 403

    k = get_karaoke_instance()
    if not k.db:
        return jsonify({"success": False, "message": _("Database not available")}), 500

    display_name = request.form.get("display_name", "").strip()
    canonical_name = request.form.get("canonical_name", "").strip() or None

    if not display_name:
        return jsonify({"success": False, "message": _("Display name required")}), 400

    success = k.db.update_play_user(play_id, display_name, canonical_name)
    if success:
        flash(_("Play user updated to: %s") % display_name, "is-success")
        return jsonify({"success": True, "message": _("Play updated")})
    else:
        return jsonify({"success": False, "message": _("Play not found")}), 404
