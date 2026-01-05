"""User preferences management routes."""

import flask_babel
from flask import Blueprint, flash, jsonify, redirect, request, url_for

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin

preferences_bp = Blueprint("preferences", __name__)

_ = flask_babel.gettext


@preferences_bp.route("/change_preferences", methods=["GET"])
def change_preferences():
    """Change a user preference setting.
    ---
    tags:
      - Preferences
    parameters:
      - name: pref
        in: query
        type: string
        required: true
        description: Preference key to change
      - name: val
        in: query
        type: string
        required: true
        description: New value for the preference
    responses:
      200:
        description: JSON result of preference change
      302:
        description: Redirects to info page if not admin
    """
    k = get_karaoke_instance()
    if is_admin():
        preference = request.args["pref"]
        val = request.args["val"]

        rc = k.change_preferences(preference, val)

        return jsonify(rc)
    else:
        # MSG: Message shown after trying to change preferences without admin permissions.
        flash(_("You don't have permission to change preferences"), "is-danger")
    return redirect(url_for("info.info"))


@preferences_bp.route("/clear_preferences", methods=["GET"])
def clear_preferences():
    """Reset all preferences to defaults.
    ---
    tags:
      - Preferences
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    if is_admin():
        rc = k.clear_preferences()
        if rc[0]:
            flash(rc[1], "is-success")
        else:
            flash(rc[1], "is-danger")
    else:
        # MSG: Message shown after trying to clear preferences without admin permissions.
        flash(_("You don't have permission to clear preferences"), "is-danger")
    return redirect(url_for("home.home"))
