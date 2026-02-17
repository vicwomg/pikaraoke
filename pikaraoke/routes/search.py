"""YouTube search and download routes."""

from __future__ import annotations

import json

import flask_babel
from flask import current_app, flash, redirect, render_template, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.youtube_dl import get_search_results

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


class SearchQuery(Schema):
    search_string = fields.String(metadata={"description": "YouTube search query"})
    non_karaoke = fields.String(
        metadata={"description": "Set to 'true' to search without appending 'karaoke' to query"}
    )


class AutocompleteQuery(Schema):
    q = fields.String(required=True, metadata={"description": "Search query for autocomplete"})


class DownloadForm(Schema):
    song_url = fields.String(required=True, metadata={"description": "YouTube URL to download"})
    song_added_by = fields.String(
        required=True, metadata={"description": "Name of the user requesting the download"}
    )
    song_title = fields.String(
        required=True, metadata={"description": "Display title for the song"}
    )
    queue = fields.String(metadata={"description": "Set to 'on' to queue the song after download"})


@search_bp.route("/search", methods=["GET"])
@search_bp.arguments(SearchQuery, location="query")
def search(query):
    """YouTube search page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    search_string = query.get("search_string")
    if search_string:
        non_karaoke = query.get("non_karaoke") == "true"
        if non_karaoke:
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
@search_bp.arguments(AutocompleteQuery, location="query")
def autocomplete(query):
    """Search available songs for autocomplete."""
    k = get_karaoke_instance()
    q = query["q"].lower()
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
@search_bp.arguments(DownloadForm, location="form")
def download(form):
    """Download a video from YouTube."""
    k = get_karaoke_instance()
    song = form["song_url"]
    user = form["song_added_by"]
    title = form["song_title"]
    queue = form.get("queue") == "on"

    # Queue the download (processed serially by the download worker)
    k.download_manager.queue_download(song, queue, user, title)

    return redirect(url_for("search.search"))
