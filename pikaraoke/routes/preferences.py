"""User preferences management routes."""

import flask_babel
from flask import flash, jsonify, redirect, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.auth import host_only
from pikaraoke.lib.current_app import broadcast_event, get_karaoke_instance
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.splash import _get_active_score_phrases

_ = flask_babel.gettext
_lazy = flask_babel.lazy_gettext

_SCORE_PHRASE_KEYS = {"low_score_phrases", "mid_score_phrases", "high_score_phrases"}

preferences_bp = Blueprint("preferences", __name__)


class ChangePreferenceForm(Schema):
    pref = fields.String(
        required=True, metadata={"description": "Name of the preference to change"}
    )
    val = fields.String(required=True, metadata={"description": "New value for the preference"})


@preferences_bp.route("/change_preferences", methods=["POST"])
@host_only(_lazy("You don't have permission to change preferences"))
@preferences_bp.arguments(ChangePreferenceForm, location="form")
def change_preferences(form):
    """Change a user preference setting."""
    k = get_karaoke_instance()
    preference = form["pref"]
    val = form["val"]
    success, message = k.preferences.set(preference, val)
    if success:
        broadcast_event("preferences_update", {"key": preference, "value": val})
        if preference in _SCORE_PHRASE_KEYS:
            broadcast_event("score_phrases_update", _get_active_score_phrases(k))
    return jsonify([success, message])


@preferences_bp.route("/clear_preferences", methods=["POST"])
@host_only(_lazy("You don't have permission to clear preferences"))
def clear_preferences():
    """Reset all preferences to defaults."""
    k = get_karaoke_instance()
    success, message = k.preferences.reset_all()
    if success:
        k.update_now_playing_socket()
        broadcast_event("preferences_reset", PreferenceManager.DEFAULTS)
        broadcast_event("score_phrases_update", _get_active_score_phrases(k))
    flash(message, "is-success" if success else "is-danger")
    return redirect(url_for("info.info"))
