"""The YouTube search page. What it calls lives in `search_api`."""

from __future__ import annotations

import logging
import subprocess

import flask_babel
from flask import flash, render_template, request
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.network import is_youtube_reachable
from pikaraoke.lib.youtube_dl import get_search_results
from pikaraoke.lib.ytdl_health import clear_ytdl_broken, raise_ytdl_broken

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


@search_bp.route("/search", methods=["GET"])
@public
def search():
    """YouTube search page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    search_string = request.args.get("search_string")
    search_results = None
    if search_string:
        non_karaoke = request.args.get("non_karaoke") == "true"
        # Quoting makes the term a hard requirement, not a ranking hint:
        # 91% karaoke results against 82% unquoted, over 60 tail results.
        query = search_string if non_karaoke else f'{search_string} "karaoke"'
        try:
            search_results = get_search_results(query)
        except (subprocess.CalledProcessError, OSError) as e:
            # Any way yt-dlp fails to run or extract (bad exit, missing or corrupt
            # binary). With YouTube reachable that means extraction is broken;
            # unreachable means offline, not yt-dlp's fault, so no alert.
            logging.error(f"yt-dlp search failed: {e}")
            if is_youtube_reachable():
                raise_ytdl_broken(k.events)
                # MSG: Flash on the search page when YouTube search is broken.
                flash(
                    _("Search is currently unavailable. Ask the host to update yt-dlp."),
                    "is-danger",
                )
            else:
                logging.warning("Search failed and YouTube is unreachable; assuming offline")
                # MSG: Flash on the search page when the box has no internet connection.
                flash(_("No internet connection, so new songs can't be searched."), "is-warning")
        else:
            clear_ytdl_broken(k.events)
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
