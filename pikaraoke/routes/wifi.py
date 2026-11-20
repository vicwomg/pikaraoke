"""Wi-Fi setup page: a dedicated screen for moving the Pi onto a network.

Admin-only and present only on a supported Pi; the JSON it drives lives in
wifi_api.py.
"""

import flask_babel
from flask import abort, render_template
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name

_ = flask_babel.gettext

wifi_bp = Blueprint("wifi", __name__)


@wifi_bp.route("/wifi")
def wifi():
    """Where an admin picks a network. 404 off a supported Pi, so nothing else sees it."""
    if not get_karaoke_instance().wifi_manager.supported:
        abort(404)
    return render_template(
        "wifi.html",
        site_title=get_site_name(),
        # MSG: Title of the Wi-Fi setup page.
        title=_("Wi-Fi"),
    )
