"""Background music streaming routes."""

import os
import random

from flask import jsonify, send_from_directory, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance

background_music_bp = Blueprint("bg_music", __name__)


def _shuffled_tracks(directory: str, limit: int = 50) -> list[str]:
    tracks = [f for f in os.listdir(directory) if f.lower().endswith((".mp3", ".mp4"))]
    random.shuffle(tracks)
    return tracks[:limit]


@background_music_bp.route("/bg_music/<file>", methods=["GET"])
def bg_music(file):
    """Stream a background music file.

    send_from_directory, not send_file: `file` comes from the URL, and only this
    refuses one that escapes the directory.
    """
    k = get_karaoke_instance()
    return send_from_directory(k.bg_music_path, file)


@background_music_bp.route("/bg_playlist", methods=["GET"])
def bg_playlist():
    """Get a randomized background music playlist."""
    k = get_karaoke_instance()
    if k.bg_music_path is None or not os.path.isdir(k.bg_music_path):
        return jsonify([])
    tracks = _shuffled_tracks(k.bg_music_path)
    return jsonify([url_for("bg_music.bg_music", file=track) for track in tracks])
