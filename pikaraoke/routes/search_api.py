"""API for previewing and downloading YouTube results."""

import flask_babel
from flask import jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.singer_manager import SINGER_NAME_MAX_LENGTH
from pikaraoke.lib.youtube_dl import canonical_youtube_url, get_stream_url

_ = flask_babel.gettext

search_api_bp = Blueprint("search_api", __name__)


def _validate_youtube_target(value: str, field_name: str) -> str:
    canonical = canonical_youtube_url(value)
    if canonical is None:
        # MSG: Error when a download or preview URL is not a valid YouTube video URL or ID
        raise ValidationError(_("Invalid YouTube URL"), field_name)
    return canonical


class PreviewQuery(Schema):
    url = fields.String(required=True, metadata={"description": "YouTube video URL to preview"})

    @validates_schema
    def check_url(self, data, **kwargs):
        data["url"] = _validate_youtube_target(data["url"], "url")


class DownloadBody(Schema):
    song_url = fields.String(required=True, metadata={"description": "YouTube URL to download"})
    song_added_by = fields.String(
        required=True,
        validate=validate.Length(max=SINGER_NAME_MAX_LENGTH),
        metadata={"description": "Name of the user requesting the download"},
    )
    song_title = fields.String(
        required=True, metadata={"description": "Display title for the song"}
    )
    queue = fields.Boolean(
        load_default=False, metadata={"description": "Whether to queue the song after download"}
    )

    @validates_schema
    def check_fields(self, data, **kwargs):
        name = data["song_added_by"]
        # Whitespace-only is blank: it would be truthy everywhere the name is read.
        if not name.strip():
            # MSG: Error when a download is requested without a name
            raise ValidationError(_("A name is required"), "song_added_by")
        if "<" in name or ">" in name:
            # MSG: Error when a singer name contains disallowed HTML angle brackets
            raise ValidationError(_("Invalid singer name"), "song_added_by")
        data["song_url"] = _validate_youtube_target(data["song_url"], "song_url")


@search_api_bp.route("/api/preview")
@public
@search_api_bp.arguments(PreviewQuery, location="query")
def preview(query):
    """Get a direct stream URL for previewing a YouTube video."""
    stream_url = get_stream_url(query["url"])
    if stream_url is None:
        return jsonify({"error": "Could not fetch stream URL"}), 500
    return jsonify({"stream_url": stream_url})


@search_api_bp.route("/api/download", methods=["POST"])
@public
@search_api_bp.arguments(DownloadBody, location="json")
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
