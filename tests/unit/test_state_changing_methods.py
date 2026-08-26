"""The routes that change state must not answer GET.

For the host-only ones the reason is the admin cookie: SameSite=Lax still
attaches it to a top-level GET navigation, so a route that mutates on GET can be
fired by a link on any page the host visits. The routes open to the room borrow
no privilege, but a state change still does not belong on the verb that link
prefetchers, previews and proxy retries are free to replay unasked.
"""

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.preferences import preferences_bp
from pikaraoke.routes.queue import queue_bp

STATE_CHANGING_ENDPOINTS = {
    "admin.update_ytdl",
    "admin.sync_library",
    "admin.quit",
    "admin.shutdown",
    "admin.reboot",
    "admin.expand_fs",
    "admin.logout",
    "admin.set_admin_password",
    "preferences.change_preferences",
    "preferences.clear_preferences",
    "files.delete_file",
    "queue.add_random",
    "queue.enqueue_form",
    "queue.queue_edit",
    "controller.skip",
    "controller.pause",
    "controller.restart",
    "controller.transpose",
    "controller.volume",
    "controller.vol_up",
    "controller.vol_down",
}


@pytest.fixture
def url_map():
    app = Flask(__name__)
    for blueprint in (admin_bp, controller_bp, files_bp, preferences_bp, queue_bp):
        app.register_blueprint(blueprint)
    return app.url_map


def test_every_listed_endpoint_is_registered(url_map):
    registered = {rule.endpoint for rule in url_map.iter_rules()}
    assert STATE_CHANGING_ENDPOINTS <= registered


@pytest.mark.parametrize("endpoint", sorted(STATE_CHANGING_ENDPOINTS))
def test_state_changing_route_rejects_get(url_map, endpoint):
    methods = {
        m for rule in url_map.iter_rules() if rule.endpoint == endpoint for m in rule.methods
    }
    assert "GET" not in methods
    assert "POST" in methods
