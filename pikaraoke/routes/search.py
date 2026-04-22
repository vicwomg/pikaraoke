"""YouTube search and download routes."""

from __future__ import annotations

import json
from threading import Thread

import flask_babel
from flask import current_app, jsonify, render_template, request, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.music_metadata import search_itunes, search_musicbrainz
from pikaraoke.lib.youtube_dl import check_captions, get_search_results, get_stream_url

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


class AutocompleteQuery(Schema):
    q = fields.String(required=True, metadata={"description": "Search query for autocomplete"})


class SuggestQuery(Schema):
    q = fields.String(
        required=True, metadata={"description": "Search query for iTunes music suggestions"}
    )


class PreviewQuery(Schema):
    url = fields.String(required=True, metadata={"description": "YouTube video URL to preview"})


class CaptionCheckQuery(Schema):
    id = fields.String(required=True, metadata={"description": "11-char YouTube video ID"})


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
def search():
    """YouTube search page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    # Skip the " karaoke" suffix when stem separation (demucs) is on —
    # we can strip vocals locally, so restricting YT results hurts recall.
    vocal_removal = k.preferences.get_or_default("vocal_removal")
    search_string = request.args.get("search_string")
    if search_string:
        non_karaoke = request.args.get("non_karaoke") == "true"
        if non_karaoke or vocal_removal:
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
        vocal_removal=vocal_removal,
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
                    "fileName": k.song_manager.display_name_from_path(each),
                    "type": "autocomplete",
                }
            )
    response = current_app.response_class(response=json.dumps(result), mimetype="application/json")
    return response


@search_bp.route("/suggest")
@search_bp.arguments(SuggestQuery, location="query")
def suggest(query):
    """iTunes + MusicBrainz music suggestions for the search box (US-1).

    Both providers run in parallel threads so their latency stacks in parallel
    rather than series. Results are merged and deduped by a
    lowercased ``"artist - track"`` key; iTunes wins when both providers
    return the same pair (its metadata is generally cleaner for karaoke).
    Each hit is tagged with ``type`` so the UI can show distinct icons.
    """
    q = query["q"]
    itunes_hits: list[dict] = []
    mb_hits: tuple[dict, ...] = ()

    def _itunes() -> None:
        nonlocal itunes_hits
        itunes_hits = search_itunes(q, limit=8)

    def _mb() -> None:
        nonlocal mb_hits
        mb_hits = search_musicbrainz(q, limit=5)

    t_it = Thread(target=_itunes, name="suggest-itunes", daemon=True)
    t_mb = Thread(target=_mb, name="suggest-mb", daemon=True)
    t_it.start()
    t_mb.start()
    # Both providers already cap their own timeout (~3s); bounded join prevents
    # a hung socket from stalling the response.
    t_it.join(timeout=4.0)
    t_mb.join(timeout=4.0)

    result: list[dict] = []
    seen: set[str] = set()

    def _emit(artist: str, track: str, type_: str, path_prefix: str) -> None:
        key = f"{artist.strip().lower()} - {track.strip().lower()}"
        if key in seen:
            return
        seen.add(key)
        result.append(
            {
                "path": f"{path_prefix}:{artist} - {track}",
                "fileName": f"{artist} - {track}",
                "type": type_,
            }
        )

    for hit in itunes_hits:
        _emit(hit["artist"], hit["track"], "itunes", "itunes")
    for hit in mb_hits:
        _emit(hit["artist"], hit["track"], "musicbrainz", "mb")

    return current_app.response_class(response=json.dumps(result), mimetype="application/json")


@search_bp.route("/preview")
@search_bp.arguments(PreviewQuery, location="query")
def preview(query):
    """Get a direct stream URL for previewing a YouTube video."""
    stream_url = get_stream_url(query["url"])
    if stream_url is None:
        return jsonify({"error": "Could not fetch stream URL"}), 500
    return jsonify({"stream_url": stream_url})


@search_bp.route("/caption-check")
@search_bp.arguments(CaptionCheckQuery, location="query")
def caption_check(query):
    """Probe a single YouTube video for caption availability.

    Used by the search-results page to lazily badge cards as "CC" so the
    user can prefer videos with existing subtitles (LRCLib/Genius/Whisper
    still run downstream, but a curated caption beats ASR every time).

    Probe cost is ~1-3s per video (full yt-dlp metadata fetch), which is
    why this endpoint is per-ID and fronted by an in-process cache —
    rendering 10 badges costs 10 probes on first visit, 0 on reloads.
    """
    return jsonify(check_captions(query["id"]))


@search_bp.route("/download", methods=["POST"])
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
