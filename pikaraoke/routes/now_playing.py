"""Now playing status endpoint."""

import json
import logging

from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance

nowplaying_bp = Blueprint("now_playing", __name__)


@nowplaying_bp.route("/now_playing")
def now_playing():
    """Get current playback status."""
    k = get_karaoke_instance()
    try:
        return json.dumps(k.get_now_playing())
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""
