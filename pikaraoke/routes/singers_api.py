"""Singer-facing API. Favourites today; rename and preferences join it later."""

import flask_babel
from flask import jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, post_load

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.singer_manager import SINGER_NAME_MAX_LENGTH, normalize_name

_ = flask_babel.gettext

singers_api_bp = Blueprint("singers_api", __name__)


class FavouriteForm(Schema):
    song = fields.String(required=True, metadata={"description": "Path to the song file"})
    starred = fields.Boolean(required=True, metadata={"description": "Desired favourite state"})
    # The singer the star belongs to, resolved client-side exactly as a queue add
    # resolves it (the cookie, else the Join in dialog), so the two agree on a name.
    song_added_by = fields.String(required=True, metadata={"description": "The singer's name"})

    @post_load
    def clean_name(self, data, **kwargs):
        # Same normalisation as a queue add, so a star and a play agree on a name.
        name = normalize_name(data["song_added_by"])
        if not name:
            # MSG: Error when a song is favourited without a name.
            raise ValidationError(_("A name is required"), "song_added_by")
        if len(name) > SINGER_NAME_MAX_LENGTH:
            # MSG: Error when a favouriting name is longer than the limit.
            raise ValidationError(
                _("Name must be %(max)d characters or fewer", max=SINGER_NAME_MAX_LENGTH),
                "song_added_by",
            )
        data["song_added_by"] = name
        return data


@singers_api_bp.route("/api/singer/favourite", methods=["POST"])
@public
@singers_api_bp.arguments(FavouriteForm, location="form")
def set_favourite(form):
    """Star or unstar a song for the named singer.

    Open to the room: a favourite borrows no privilege, and the name is the
    singer's own, exactly as a queue add is.
    """
    k = get_karaoke_instance()
    name = form["song_added_by"]
    if form["starred"]:
        k.singer_manager.add_favourite(name, form["song"])
    else:
        k.singer_manager.remove_favourite(name, form["song"])
    return jsonify({"success": True, "starred": form["starred"]})
