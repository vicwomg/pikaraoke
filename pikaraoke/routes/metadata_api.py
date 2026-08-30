"""API endpoints for song metadata: iTunes suggestions."""

from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.auth import answers_json
from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.metadata_parser import regex_tidy
from pikaraoke.lib.metadata_providers import get_provider, suggest_metadata

metadata_bp = Blueprint("metadata", __name__)


class AutoFormatQuery(Schema):
    filename = fields.String(required=True)


class SuggestNamesQuery(Schema):
    filename = fields.String(required=True)
    limit = fields.Integer(load_default=5)
    country = fields.String(load_default=None)


@metadata_bp.route("/api/metadata/auto-format")
@answers_json
@metadata_bp.arguments(AutoFormatQuery, location="query")
def auto_format(query):
    """Apply regex_tidy to a filename and return the formatted result."""
    formatted = regex_tidy(query["filename"])
    return {"formatted_name": formatted or query["filename"]}


@metadata_bp.route("/api/metadata/suggest-names")
@answers_json
@metadata_bp.arguments(SuggestNamesQuery, location="query")
def suggest_names(query):
    """Search for track suggestions matching a filename."""
    k = get_karaoke_instance()
    provider = get_provider(k.preferences, country=query["country"])
    results = suggest_metadata(
        query["filename"],
        provider=provider,
        limit=query["limit"],
        artist_first=k.preferences.get_or_default("suggestion_name_order") == "artist_title",
    )
    return {"suggestions": results}
