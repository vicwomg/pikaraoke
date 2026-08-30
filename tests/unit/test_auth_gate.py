"""The authorization gate: every endpoint is decided, and refusals are uniform."""

import pytest
from flask import Flask
from flask_babel import Babel
from flask_smorest import Api

from pikaraoke.lib.auth import answers_json, install_auth_gate, public
from pikaraoke.routes import API_BLUEPRINTS, INTERNAL_BLUEPRINTS
from tests.conftest import StubAdminAuth


@pytest.fixture
def real_app():
    """Every blueprint the app registers, wired the same way.

    Off the same lists app.py registers, so a new blueprint cannot reach the app
    while missing here. Built rather than imported from app.py, which parses
    argv and opens the data directory at import.
    """
    app = Flask(__name__)
    app.config.update(
        API_TITLE="PiKaraoke", API_VERSION="test", OPENAPI_VERSION="3.0.2", OPENAPI_URL_PREFIX="/"
    )
    api = Api(app)
    for bp in API_BLUEPRINTS:
        api.register_blueprint(bp)
    for bp in INTERNAL_BLUEPRINTS:
        app.register_blueprint(bp)
    return app


# Every endpoint the room may reach. Opening a route to guests means editing
# this list, which is a line in a diff someone has to agree with.
EXPECTED_PUBLIC_ENDPOINTS = {
    "admin.auth",
    "admin.logout",
    "bg_music.bg_music",
    "bg_music.bg_playlist",
    "files.browse",
    "home.home",
    "images.logo",
    "images.qrcode",
    "info.info",
    "now_playing.now_playing",
    "queue.delete_download_error",
    "queue.enqueue_form",
    "queue.get_current_downloads",
    "queue.get_queue",
    "queue.queue",
    "queue.retry_download_error",
    "search.download",
    "search.preview",
    "search.search",
    "sessions.history",
    "sessions.rankings",
    "sessions_api.get_plays",
    "splash.get_score_phrases",
    "splash.splash",
    "stream.stream_bg_video",
    "stream.stream_full",
    "stream.stream_init",
    "stream.stream_main",
    "stream.stream_playlist",
    "stream.stream_progressive_mp4",
    "stream.stream_segment",
    "stream.stream_segment_m4s",
    "stream.stream_subtitle",
}


def test_every_endpoint_is_decided(real_app):
    """Every route is public by explicit mark, or host-only. No third state."""
    marked = {
        rule.endpoint
        for rule in real_app.url_map.iter_rules()
        if getattr(real_app.view_functions[rule.endpoint], "pika_public", False)
    }
    assert marked == EXPECTED_PUBLIC_ENDPOINTS


def _gated_app(admin: bool):
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin)
    Babel(app)
    app.add_url_rule("/", "home.home", public(lambda: "home"))
    app.add_url_rule("/open", "open", public(lambda: "open"))
    app.add_url_rule("/closed", "closed", lambda: "closed")
    app.add_url_rule("/api/closed", "api_closed", lambda: "closed")
    app.add_url_rule("/json", "json", answers_json(lambda: "x"))
    install_auth_gate(app)
    return app


class TestRefusal:
    """One shape for the whole app, chosen by what the caller is reading."""

    @pytest.fixture
    def guest(self):
        return _gated_app(admin=False).test_client()

    def test_an_unmarked_route_refuses_a_guest(self, guest):
        assert guest.get("/closed").status_code == 302

    def test_a_marked_route_answers_a_guest(self, guest):
        assert guest.get("/open").status_code == 200

    def test_the_admin_reaches_everything(self):
        client = _gated_app(admin=True).test_client()
        assert client.get("/closed").status_code == 200
        assert client.get("/api/closed").status_code == 200

    def test_an_api_path_refuses_in_json(self, guest):
        response = guest.get("/api/closed")
        assert response.status_code == 403
        assert response.get_json() == {"error": "Unauthorized"}

    def test_an_xhr_refuses_in_json(self, guest):
        response = guest.get("/closed", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 403

    def test_a_json_route_refuses_in_json_without_sniffing(self, guest):
        """Declared rather than guessed: no /api prefix, no XHR header."""
        response = guest.get("/json")
        assert response.status_code == 403
        assert response.get_json() == {"error": "Unauthorized"}

    def test_a_page_refusal_says_so(self, guest):
        """The redirect carries the reason, or it reads as a broken link."""
        guest.get("/closed")
        with guest.session_transaction() as session:
            assert "permission" in str(session["_flashes"])

    def test_an_unmatched_path_is_a_404_not_a_refusal(self, guest):
        assert guest.get("/nothing-here").status_code == 404
