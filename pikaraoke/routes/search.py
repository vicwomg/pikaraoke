"""YouTube search and download routes."""

import json

import flask_babel
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


@search_bp.route("/search", methods=["GET"])
def search():
    """YouTube search page.
    ---
    tags:
      - Pages
    parameters:
      - name: search_string
        in: query
        type: string
        description: YouTube search query
      - name: non_karaoke
        in: query
        type: string
        description: Set to 'true' to include non-karaoke results
    responses:
      200:
        description: HTML search page with results
    """
    k = get_karaoke_instance()
    site_name = get_site_name()
    if "search_string" in request.args:
        search_string = request.args["search_string"]
        if "non_karaoke" in request.args and request.args["non_karaoke"] == "true":
            search_results = k.get_search_results(search_string)
        else:
            search_results = k.get_karaoke_search_results(search_string)
    else:
        search_string = None
        search_results = None
    return render_template(
        "search.html",
        site_title=site_name,
        title="Search",
        songs=k.available_songs,
        search_results=search_results,
        search_string=search_string,
    )


@search_bp.route("/autocomplete")
def autocomplete():
    """Search available songs for autocomplete.
    ---
    tags:
      - Search
    parameters:
      - name: q
        in: query
        type: string
        required: true
        description: Search query string
    responses:
      200:
        description: List of matching songs
        schema:
          type: array
          items:
            type: object
            properties:
              path:
                type: string
                description: File path of the song
              fileName:
                type: string
                description: Display name of the song
              type:
                type: string
                description: Result type (autocomplete)
    """
    k = get_karaoke_instance()
    q = request.args.get("q").lower()
    result = []
    for each in k.available_songs:
        if q in each.lower():
            result.append(
                {
                    "path": each,
                    "fileName": k.filename_from_path(each),
                    "type": "autocomplete",
                }
            )
    response = current_app.response_class(response=json.dumps(result), mimetype="application/json")
    return response


@search_bp.route("/download", methods=["POST"])
def download():
    """Download a video from YouTube.
    ---
    tags:
      - Search
    consumes:
      - application/x-www-form-urlencoded
    parameters:
      - name: song-url
        in: formData
        type: string
        required: true
        description: YouTube video URL
      - name: song-added-by
        in: formData
        type: string
        required: true
        description: Username initiating download
      - name: song-title
        in: formData
        type: string
        description: Display title for the song
      - name: queue
        in: formData
        type: string
        description: Set to 'on' to add to queue after download
    responses:
      302:
        description: Redirects to search page
    """
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
    k.download_video(song, queue, user, title)

    return redirect(url_for("search.search"))
