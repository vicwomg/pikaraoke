"""Now playing status endpoint."""

import json
import logging

from flask import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance

nowplaying_bp = Blueprint("now_playing", __name__)


@nowplaying_bp.route("/now_playing")
def now_playing():
    """Get current playback status.
    ---
    tags:
      - Status
    responses:
      200:
        description: Current playback state
        schema:
          type: object
          properties:
            now_playing:
              type: string
              description: Title of current song (null if none)
            now_playing_user:
              type: string
              description: User who queued the song
            now_playing_duration:
              type: integer
              description: Song duration in seconds
            now_playing_transpose:
              type: integer
              description: Current transpose value
            now_playing_url:
              type: string
              description: Stream URL for the song
            up_next:
              type: string
              description: Title of next song in queue
            next_user:
              type: string
              description: User who queued the next song
            is_paused:
              type: boolean
              description: Whether playback is paused
            volume:
              type: number
              description: Current volume (0.0-1.0)
    """
    k = get_karaoke_instance()
    try:
        return json.dumps(k.get_now_playing())
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""
