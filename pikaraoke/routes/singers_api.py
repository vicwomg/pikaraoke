"""Singer-facing API. Favourites and self-service rename; preferences join later."""

import flask_babel
from flask import jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, ValidationError, fields, post_load

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import current_singer_name, get_karaoke_instance
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


def _clean_singer_name(raw: str, field: str) -> str:
    """Normalise and bound a singer name, the same rule the queue add applies."""
    name = normalize_name(raw)
    if not name:
        # MSG: Error when a rename is submitted without a new name.
        raise ValidationError(_("A name is required"), field)
    if len(name) > SINGER_NAME_MAX_LENGTH:
        # MSG: Error when a renaming name is longer than the limit.
        raise ValidationError(
            _("Name must be %(max)d characters or fewer", max=SINGER_NAME_MAX_LENGTH), field
        )
    return name


class RenameForm(Schema):
    old_name = fields.String(required=True, metadata={"description": "The singer's current name"})
    new_name = fields.String(required=True, metadata={"description": "The name to move to"})

    @post_load
    def clean(self, data, **kwargs):
        # old_name is only ever matched against the caller's cookie, so it needs
        # the same canonical form but not the blank/length refusal -- a bad old
        # name simply fails that match.
        data["old_name"] = normalize_name(data["old_name"])
        data["new_name"] = _clean_singer_name(data["new_name"], "new_name")
        return data


@singers_api_bp.route("/api/singer/rename", methods=["POST"])
@public
@singers_api_bp.arguments(RenameForm, location="form")
def rename_singer(form):
    """Rename the singer this device is holding, carrying everything they own.

    Open to the room, but you can only rename the name in your own cookie: the
    rename rewrites this device's name on success, so a request for any other
    name has no device to re-home and is refused. Renaming onto a free name
    moves the queue, the song on screen, history and favourites together;
    renaming onto a name that already owns data is a merge, which is the admin's.
    """
    cookie = current_singer_name()
    if cookie is None or normalize_name(cookie).lower() != form["old_name"].lower():
        # MSG: Error when a device tries to rename a name it is not holding.
        return jsonify({"success": False, "error": _("You can only change your own name")}), 403

    k = get_karaoke_instance()
    outcome = k.rename_singer(form["old_name"], form["new_name"])
    if outcome == "taken":
        # MSG: Error when renaming onto a name that already belongs to someone else.
        return jsonify({"success": False, "error": _("That name is already taken")}), 409
    if outcome == "reserved":
        # MSG: Error when renaming to or from a reserved system name.
        return jsonify({"success": False, "error": _("That name is reserved")}), 409
    return jsonify({"success": True, "name": form["new_name"], "action": outcome})
