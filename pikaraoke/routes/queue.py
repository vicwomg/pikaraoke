"""The queue page. What it calls lives in `queue_api`."""

from __future__ import annotations

import flask_babel
from flask import render_template
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

queue_bp = Blueprint("queue", __name__)


@queue_bp.route("/queue")
@public
def queue():
    """Queue management page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    return render_template(
        "queue.html",
        queue=k.queue_manager.queue,
        site_title=site_name,
        # MSG: Title of the queue page.
        title=_("Queue"),
        admin=is_admin(),
    )
