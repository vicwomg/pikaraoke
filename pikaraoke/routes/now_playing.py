"""Now playing status endpoint."""

import logging

from flask import jsonify
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance

nowplaying_bp = Blueprint("now_playing", __name__)


@nowplaying_bp.route("/api/now_playing")
@public
def now_playing():
    """Get current playback status."""
    k = get_karaoke_instance()
    try:
        return jsonify(k.get_now_playing())
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""
