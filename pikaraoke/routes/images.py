"""Image serving routes for QR code and logo."""

import os

import flask_babel
from flask import send_file
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance

_ = flask_babel.gettext

images_bp = Blueprint("images", __name__)


@images_bp.route("/qrcode")
@public
def qrcode():
    """Get QR code image for the web interface URL."""
    k = get_karaoke_instance()
    return send_file(k.qr_code_path, mimetype="image/png")


@images_bp.route("/logo")
@public
def logo():
    """Get the PiKaraoke logo image."""
    k = get_karaoke_instance()
    return send_file(os.path.abspath(k.logo_path), mimetype="image/png")
