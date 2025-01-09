import hashlib
import json
import logging

from flask import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance

nowplaying_bp = Blueprint("now_playing", __name__)


@nowplaying_bp.route("/nowplaying")
def nowplaying():
    k = get_karaoke_instance()
    try:
        if len(k.queue) >= 1:
            next_song = k.queue[0]["title"]
            next_user = k.queue[0]["user"]
        else:
            next_song = None
            next_user = None
        rc = {
            "now_playing": k.now_playing,
            "now_playing_user": k.now_playing_user,
            "now_playing_command": k.now_playing_command,
            "now_playing_duration": k.now_playing_duration,
            "now_playing_transpose": k.now_playing_transpose,
            "now_playing_url": k.now_playing_url,
            "up_next": next_song,
            "next_user": next_user,
            "is_paused": k.is_paused,
            "volume": k.volume,
            # "is_transpose_enabled": k.is_transpose_enabled,
        }
        hash = hashlib.md5(
            json.dumps(rc, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
        ).hexdigest()
        rc["hash"] = hash  # used to detect changes in the now playing data
        return json.dumps(rc)
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""
