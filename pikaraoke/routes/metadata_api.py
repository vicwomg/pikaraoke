"""API endpoints for song metadata: iTunes suggestions."""

from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.metadata_providers import get_provider, suggest_metadata

metadata_bp = Blueprint("metadata", __name__)


class SuggestNamesQuery(Schema):
    filename = fields.String(required=True)
    limit = fields.Integer(load_default=5)
    country = fields.String(load_default=None)


@metadata_bp.route("/metadata/suggest-names")
@metadata_bp.arguments(SuggestNamesQuery, location="query")
def suggest_names(query):
    """Search for track suggestions matching a filename."""
    k = get_karaoke_instance()
    provider = get_provider(k.preferences, country=query["country"])
    results = suggest_metadata(query["filename"], provider=provider, limit=query["limit"])
    return {"suggestions": results}
