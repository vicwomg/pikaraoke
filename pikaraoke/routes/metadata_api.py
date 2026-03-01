"""API endpoints for song metadata: tidy names and Last.fm suggestions."""

from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.metadata_parser import regex_tidy, search_lastfm_tracks

metadata_bp = Blueprint("metadata", __name__)


class TidyNameQuery(Schema):
    filename = fields.String(required=True)


class SuggestNamesQuery(Schema):
    filename = fields.String(required=True)
    limit = fields.Integer(load_default=5)


@metadata_bp.route("/metadata/tidy-name")
@metadata_bp.arguments(TidyNameQuery, location="query")
def tidy_name(query):
    """Apply regex-based cleanup to a song filename."""
    return {"tidied": regex_tidy(query["filename"])}


@metadata_bp.route("/metadata/suggest-names")
@metadata_bp.arguments(SuggestNamesQuery, location="query")
def suggest_names(query):
    """Search Last.fm for track suggestions matching a filename."""
    results = search_lastfm_tracks(query["filename"], limit=query["limit"])
    return {"suggestions": results}
