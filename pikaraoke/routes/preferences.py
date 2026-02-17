"""User preferences management routes."""

from __future__ import annotations

import flask_babel
from flask import flash, jsonify, redirect, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin

preferences_bp = Blueprint("preferences", __name__)

_ = flask_babel.gettext


class ChangePreferenceQuery(Schema):
    pref = fields.String(
        required=True, metadata={"description": "Name of the preference to change"}
    )
    val = fields.String(required=True, metadata={"description": "New value for the preference"})


@preferences_bp.route("/change_preferences", methods=["GET"])
@preferences_bp.arguments(ChangePreferenceQuery, location="query")
def change_preferences(query):
    """Change a user preference setting."""
    k = get_karaoke_instance()
    if is_admin():
        preference = query["pref"]
        val = query["val"]
        success, message = k.preferences.set(preference, val)
        return jsonify([success, message])
    else:
        # MSG: Message shown after trying to change preferences without admin permissions.
        flash(_("You don't have permission to change preferences"), "is-danger")
    return redirect(url_for("info.info"))


@preferences_bp.route("/clear_preferences", methods=["GET"])
def clear_preferences():
    """Reset all preferences to defaults."""
    k = get_karaoke_instance()
    if is_admin():
        success, message = k.preferences.reset_all()
        if success:
            k.update_now_playing_socket()
        flash(message, "is-success" if success else "is-danger")
    else:
        # MSG: Message shown after trying to clear preferences without admin permissions.
        flash(_("You don't have permission to clear preferences"), "is-danger")
    return redirect(url_for("info.info"))
