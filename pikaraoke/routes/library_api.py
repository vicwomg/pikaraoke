"""API for the song library: its size, and a rescan of the filesystem."""

from flask import jsonify
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance

library_bp = Blueprint("library", __name__)


@library_bp.route("/api/library_stats")
def library_stats():
    """Return song count for the admin dashboard."""
    k = get_karaoke_instance()
    return jsonify({"song_count": len(k.song_manager.songs)})


@library_bp.route("/api/sync_library", methods=["POST"])
def sync_library():
    """Trigger a background library scan."""
    k = get_karaoke_instance()
    started = k.sync_library()
    if started:
        return jsonify({"status": "started"})
    return jsonify({"status": "already_syncing"})
