import json
import threading

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
    k = get_karaoke_instance()
    q = request.args.get("q").lower()
    result = []
    for each in k.available_songs:
        if q in each.lower():
            result.append(
                {"path": each, "fileName": k.filename_from_path(each), "type": "autocomplete"}
            )
    response = current_app.response_class(response=json.dumps(result), mimetype="application/json")
    return response


@search_bp.route("/download", methods=["POST"])
def download():
    k = get_karaoke_instance()
    d = request.form.to_dict()
    song = d["song-url"]
    user = d["song-added-by"]
    title = d["song-title"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=k.download_video, args=[song, queue, user, title])
    t.daemon = True
    t.start()

    displayed_title = title if title else song
    flash_message = (
        # MSG: Message shown after starting a download. Song title is displayed in the message.
        _("Download started: %s. This may take a couple of minutes to complete.")
        % displayed_title
    )

    if queue:
        # MSG: Message shown after starting a download that will be adding a song to the queue.
        flash_message += _("Song will be added to queue.")
    else:
        # MSG: Message shown after after starting a download.
        flash_message += _('Song will appear in the "available songs" list.')
    flash(flash_message, "is-info")
    return redirect(url_for("search.search"))
