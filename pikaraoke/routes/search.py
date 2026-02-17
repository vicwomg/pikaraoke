"""YouTube search and download routes."""

import json

import flask_babel
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.youtube_dl import get_search_results

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


@search_bp.route("/search", methods=["GET"])
@search_bp.doc(
    parameters=[
        {
            "name": "search_string",
            "in": "query",
            "schema": {"type": "string"},
            "description": "YouTube search query",
        },
        {
            "name": "non_karaoke",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Set to 'true' to search without appending 'karaoke' to query",
        },
    ]
)
def search():
    """YouTube search page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    if "search_string" in request.args:
        search_string = request.args["search_string"]
        if "non_karaoke" in request.args and request.args["non_karaoke"] == "true":
            search_results = get_search_results(search_string)
        else:
            search_results = get_search_results(search_string + " karaoke")
    else:
        search_string = None
        search_results = None
    return render_template(
        "search.html",
        site_title=site_name,
        title="Search",
        songs=k.song_manager.songs,
        search_results=search_results,
        search_string=search_string,
    )


@search_bp.route("/autocomplete")
@search_bp.doc(
    parameters=[
        {
            "name": "q",
            "in": "query",
            "schema": {"type": "string"},
            "required": True,
            "description": "Search query for autocomplete",
        },
    ]
)
def autocomplete():
    """Search available songs for autocomplete."""
    k = get_karaoke_instance()
    q = request.args.get("q").lower()
    result = []
    for each in k.song_manager.songs:
        if q in each.lower():
            result.append(
                {
                    "path": each,
                    "fileName": k.song_manager.filename_from_path(each),
                    "type": "autocomplete",
                }
            )
    response = current_app.response_class(response=json.dumps(result), mimetype="application/json")
    return response


@search_bp.route("/download", methods=["POST"])
@search_bp.doc(
    parameters=[
        {
            "name": "song-url",
            "in": "formData",
            "schema": {"type": "string"},
            "required": True,
            "description": "YouTube URL to download",
        },
        {
            "name": "song-added-by",
            "in": "formData",
            "schema": {"type": "string"},
            "required": True,
            "description": "Name of the user requesting the download",
        },
        {
            "name": "song-title",
            "in": "formData",
            "schema": {"type": "string"},
            "required": True,
            "description": "Display title for the song",
        },
        {
            "name": "queue",
            "in": "formData",
            "schema": {"type": "string"},
            "description": "Set to 'on' to queue the song after download",
        },
    ]
)
def download():
    """Download a video from YouTube."""
    k = get_karaoke_instance()
    d = request.form.to_dict()
    song = d["song-url"]
    user = d["song-added-by"]
    title = d["song-title"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # Queue the download (processed serially by the download worker)
    k.download_manager.queue_download(song, queue, user, title)

    return redirect(url_for("search.search"))
