import flask_babel
from flask import Blueprint, render_template

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext


home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def home():
    k = get_karaoke_instance()
    site_name = get_site_name()
    return render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        transpose_value=k.now_playing_transpose,
        admin=is_admin(),
        is_transpose_enabled=k.is_transpose_enabled,
    )
