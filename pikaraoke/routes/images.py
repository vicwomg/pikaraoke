import flask_babel
from flask import Blueprint, send_file

from pikaraoke.lib.current_app import get_karaoke_instance

_ = flask_babel.gettext

images_bp = Blueprint("images", __name__)


@images_bp.route("/qrcode")
def qrcode():
    k = get_karaoke_instance()
    return send_file(k.qr_code_path, mimetype="image/png")


@images_bp.route("/logo")
def logo():
    k = get_karaoke_instance()
    return send_file(k.logo_path, mimetype="image/png")
