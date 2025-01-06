import os
import random
import urllib

import flask_babel
from flask import Blueprint, jsonify, send_file

from pikaraoke.lib.current_app import get_karaoke_instance

background_music_bp = Blueprint("bg_music", __name__)

_ = flask_babel.gettext


def create_randomized_playlist(input_directory, base_url, max_songs=50):
    # Get all mp3 files in the given directory
    files = [
        f
        for f in os.listdir(input_directory)
        if f.lower().endswith(".mp3") or f.lower().endswith(".mp4")
    ]

    # Shuffle the list of mp3 files
    random.shuffle(files)
    files = files[:max_songs]

    # Create the playlist
    playlist = []
    for mp3 in files:
        mp3 = urllib.parse.quote(mp3.encode("utf8"))
        url = f"{base_url}/{mp3}"
        playlist.append(f"{url}")

    return playlist


# Routes for streaming background music
@background_music_bp.route("/bg_music/<file>", methods=["GET"])
def bg_music(file):
    k = get_karaoke_instance()
    mp3_path = os.path.join(k.bg_music_path, file)
    return send_file(mp3_path, mimetype="audio/mpeg")


# Route for getting the randomized background music playlist
@background_music_bp.route("/bg_playlist", methods=["GET"])
def bg_playlist():
    k = get_karaoke_instance()
    if (k.bg_music_path == None) or (not os.path.exists(k.bg_music_path)):
        return jsonify([])
    playlist = create_randomized_playlist(k.bg_music_path, "/bg_music", 50)
    return jsonify(playlist)
