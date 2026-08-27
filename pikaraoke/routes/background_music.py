"""Background music streaming routes."""

import os
import random

from flask import abort, jsonify, send_from_directory, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance

background_music_bp = Blueprint("bg_music", __name__)

_TRACK_EXTENSIONS = (".mp3", ".mp4")


def _track_directory(path: str) -> str:
    """The folder tracks are served from, whether the path names one or holds them."""
    return os.path.dirname(path) if os.path.isfile(path) else path


def _shuffled_tracks(path: str, limit: int = 50) -> list[str]:
    if os.path.isfile(path):
        name = os.path.basename(path)
        return [name] if name.lower().endswith(_TRACK_EXTENSIONS) else []
    tracks = [f for f in os.listdir(path) if f.lower().endswith(_TRACK_EXTENSIONS)]
    random.shuffle(tracks)
    return tracks[:limit]


@background_music_bp.route("/bg_music/<file>", methods=["GET"])
def bg_music(file):
    """Stream a background music file.

    send_from_directory, not send_file: `file` comes from the URL, and only this
    refuses one that escapes the directory.
    """
    k = get_karaoke_instance()
    if k.bg_music_path is None:
        abort(404)
    # A path naming one track serves that track only. Its neighbours are in the
    # same folder, and pointing at a file is not consent to share them.
    if os.path.isfile(k.bg_music_path) and file != os.path.basename(k.bg_music_path):
        abort(404)
    return send_from_directory(_track_directory(k.bg_music_path), file)


@background_music_bp.route("/bg_playlist", methods=["GET"])
def bg_playlist():
    """Get a randomized background music playlist."""
    k = get_karaoke_instance()
    if k.bg_music_path is None or not os.path.exists(k.bg_music_path):
        return jsonify([])
    tracks = _shuffled_tracks(k.bg_music_path)
    return jsonify([url_for("bg_music.bg_music", file=track) for track in tracks])
