"""Singer-facing API. Favourites today; rename and preferences join it later."""

import flask_babel
from flask import jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, validates_schema

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance

_ = flask_babel.gettext

singers_api_bp = Blueprint("singers_api", __name__)


class FavouriteForm(Schema):
    song = fields.String(required=True, metadata={"description": "Path to the song file"})
    starred = fields.Boolean(required=True, metadata={"description": "Desired favourite state"})
    # The singer the star belongs to, resolved client-side exactly as a queue add
    # resolves it (the cookie, else the Join in dialog), so the two agree on a name.
    song_added_by = fields.String(required=True, metadata={"description": "The singer's name"})

    @validates_schema
    def check_name(self, data, **kwargs):
        # Whitespace-only is blank: a favourite with no name has nothing to hang on.
        if not data["song_added_by"].strip():
            # MSG: Error when a song is favourited without a name.
            raise ValidationError(_("A name is required"), "song_added_by")


@singers_api_bp.route("/api/singer/favourite", methods=["POST"])
@public
@singers_api_bp.arguments(FavouriteForm, location="form")
def set_favourite(form):
    """Star or unstar a song for the named singer.

    Open to the room: a favourite borrows no privilege, and the name is the
    singer's own, exactly as a queue add is.
    """
    k = get_karaoke_instance()
    name = form["song_added_by"].strip()
    if form["starred"]:
        k.singer_manager.add_favourite(name, form["song"])
    else:
        k.singer_manager.remove_favourite(name, form["song"])
    return jsonify({"success": True, "starred": form["starred"]})
