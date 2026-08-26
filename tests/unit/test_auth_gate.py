"""The authorization gate: every endpoint is decided, and refusals are uniform."""

import pytest
from flask import Flask
from flask_babel import Babel
from flask_smorest import Api

from pikaraoke.lib.auth import host_only, install_auth_gate, public
from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.background_music import background_music_bp
from pikaraoke.routes.batch_song_renamer import batch_song_renamer_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.home import home_bp
from pikaraoke.routes.images import images_bp
from pikaraoke.routes.info import info_bp
from pikaraoke.routes.metadata_api import metadata_bp
from pikaraoke.routes.now_playing import nowplaying_bp
from pikaraoke.routes.preferences import preferences_bp
from pikaraoke.routes.queue import queue_bp
from pikaraoke.routes.search import search_bp
from pikaraoke.routes.sessions import sessions_bp
from pikaraoke.routes.sessions_api import sessions_api_bp
from pikaraoke.routes.splash import splash_bp
from pikaraoke.routes.stream import stream_bp
from tests.conftest import StubAdminAuth


@pytest.fixture
def real_app():
    """Every blueprint the app registers, wired the same way.

    Built here rather than imported from app.py, which parses argv and opens the
    data directory at import.
    """
    app = Flask(__name__)
    app.config.update(
        API_TITLE="PiKaraoke", API_VERSION="test", OPENAPI_VERSION="3.0.2", OPENAPI_URL_PREFIX="/"
    )
    api = Api(app)
    for bp in (
        queue_bp,
        search_bp,
        files_bp,
        preferences_bp,
        admin_bp,
        controller_bp,
        background_music_bp,
        images_bp,
        nowplaying_bp,
        stream_bp,
        metadata_bp,
        sessions_api_bp,
    ):
        api.register_blueprint(bp)
    for bp in (home_bp, info_bp, splash_bp, batch_song_renamer_bp, sessions_bp):
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
    "metadata.auto_format",
    "metadata.suggest_names",
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


def test_no_marker_means_host_only(real_app):
    """The property the old convention lacked: a forgotten decision fails closed."""
    decided = EXPECTED_PUBLIC_ENDPOINTS | {"static", "api-docs.openapi_json"}
    undecided = {r.endpoint for r in real_app.url_map.iter_rules()} - decided
    assert undecided, "expected host-only endpoints to exist"
    for endpoint in undecided:
        view = real_app.view_functions[endpoint]
        assert not getattr(view, "pika_public", False)


def _gated_app(admin: bool):
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin)
    Babel(app)
    app.add_url_rule("/", "home.home", public(lambda: "home"))
    app.add_url_rule("/open", "open", public(lambda: "open"))
    app.add_url_rule("/closed", "closed", lambda: "closed")
    app.add_url_rule("/api/closed", "api_closed", lambda: "closed")
    app.add_url_rule("/named", "named", host_only("bespoke refusal")(lambda: "x"))
    app.add_url_rule("/json", "json", host_only(json=True)(lambda: "x"))
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

    def test_a_bespoke_message_survives(self, guest):
        guest.get("/named")
        with guest.session_transaction() as session:
            assert "bespoke refusal" in str(session["_flashes"])

    def test_an_unmatched_path_is_a_404_not_a_refusal(self, guest):
        assert guest.get("/nothing-here").status_code == 404
