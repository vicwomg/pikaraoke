"""Login for programs: the door a client reading /apidocs comes through.

The browser's `POST /auth` answers in flashes and redirects, which a script
cannot read; this one answers in status codes.
"""

from flask import jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.auth import log_in, public
from pikaraoke.lib.current_app import get_admin_auth, is_admin

auth_api_bp = Blueprint("auth_api", __name__)


class LoginSchema(Schema):
    admin_password = fields.String(required=True, metadata={"description": "The admin password"})


@auth_api_bp.route("/api/auth")
@public
def auth_status():
    """Report whether the caller is an admin, and whether a password is wanted at all."""
    return jsonify(
        {"authenticated": is_admin(), "password_required": get_admin_auth().is_password_set()}
    )


@auth_api_bp.route("/api/auth", methods=["POST"])
@public
@auth_api_bp.arguments(LoginSchema)
def login(credentials):
    """Exchange the admin password for a session cookie.

    The cookie comes back in `Set-Cookie`, which browsers hide from scripts, so
    this console cannot show it. Call a host-only endpoint to confirm it worked.
    """
    if not get_admin_auth().is_password_set():
        # Everyone is an admin already; a 401 here would read as a locked door.
        return jsonify({"authenticated": True})
    if not log_in(credentials["admin_password"]):
        return jsonify({"error": "Incorrect admin password"}), 401
    return jsonify({"authenticated": True})
