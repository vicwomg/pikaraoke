"""YouTube search and download routes."""

from __future__ import annotations

import flask_babel
from flask import jsonify, render_template, request
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.youtube_dl import get_search_results, get_stream_url

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


class PreviewQuery(Schema):
    url = fields.String(required=True, metadata={"description": "YouTube video URL to preview"})


class DownloadBody(Schema):
    song_url = fields.String(required=True, metadata={"description": "YouTube URL to download"})
    song_added_by = fields.String(
        required=True, metadata={"description": "Name of the user requesting the download"}
    )
    song_title = fields.String(
        required=True, metadata={"description": "Display title for the song"}
    )
    queue = fields.Boolean(
        load_default=False, metadata={"description": "Whether to queue the song after download"}
    )


@search_bp.route("/search", methods=["GET"])
@public
def search():
    """YouTube search page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    search_string = request.args.get("search_string")
    if search_string:
        non_karaoke = request.args.get("non_karaoke") == "true"
        if non_karaoke:
            search_results = get_search_results(search_string)
        else:
            search_results = get_search_results(search_string + " karaoke")
    else:
        search_string = None
        search_results = None
    # A result already on this machine gets a queue action instead of a pointless
    # second download. One indexed query for the page, not one per result.
    library_matches = (
        k.db.get_paths_by_youtube_ids([r.video_id for r in search_results])
        if search_results
        else {}
    )
    return render_template(
        "search.html",
        site_title=site_name,
        # MSG: Title of the page used to get new songs into the library.
        title=_("Add New"),
        search_results=search_results,
        search_string=search_string,
        library_matches=library_matches,
    )


@search_bp.route("/preview")
@public
@search_bp.arguments(PreviewQuery, location="query")
def preview(query):
    """Get a direct stream URL for previewing a YouTube video."""
    stream_url = get_stream_url(query["url"])
    if stream_url is None:
        return jsonify({"error": "Could not fetch stream URL"}), 500
    return jsonify({"stream_url": stream_url})


@search_bp.route("/download", methods=["POST"])
@public
@search_bp.arguments(DownloadBody, location="json")
def download(form):
    """Download a video from YouTube."""
    k = get_karaoke_instance()
    song = form["song_url"]
    user = form["song_added_by"]
    title = form["song_title"]
    queue = form.get("queue", False)

    # Queue the download (processed serially by the download worker)
    k.download_manager.queue_download(song, queue, user, title)

    return jsonify({"status": "ok"})
