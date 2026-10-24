"""Tests for the singer-facing API and the identity-cookie helper."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_babel import Babel

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.auth import install_auth_gate, public
from pikaraoke.lib.current_app import current_singer_name
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.singers_api import singers_api_bp


@pytest.fixture
def app(tmp_path):
    store = AdminAuth(PreferenceManager(str(tmp_path / "config.ini")))
    test_app = Flask(__name__)
    test_app.secret_key = store.secret_key
    test_app.config["ADMIN_AUTH"] = store
    Babel(test_app)
    test_app.register_blueprint(singers_api_bp)
    test_app.add_url_rule("/", endpoint="home.home", view_func=public(lambda: "home"))
    install_auth_gate(test_app)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def karaoke():
    with patch("pikaraoke.routes.singers_api.get_karaoke_instance") as get_instance:
        k = MagicMock()
        get_instance.return_value = k
        yield k


class TestSetFavourite:
    def test_starring_stars_for_the_named_singer(self, client, karaoke):
        resp = client.post(
            "/api/singer/favourite",
            data={"song": "/s/a.mp4", "starred": "true", "song_added_by": "Alice"},
        )
        assert resp.status_code == 200
        karaoke.singer_manager.add_favourite.assert_called_once_with("Alice", "/s/a.mp4")
        karaoke.singer_manager.remove_favourite.assert_not_called()

    def test_unstarring_clears_the_star(self, client, karaoke):
        client.post(
            "/api/singer/favourite",
            data={"song": "/s/a.mp4", "starred": "false", "song_added_by": "Alice"},
        )
        karaoke.singer_manager.remove_favourite.assert_called_once_with("Alice", "/s/a.mp4")
        karaoke.singer_manager.add_favourite.assert_not_called()

    def test_a_blank_name_is_refused_and_nothing_is_saved(self, client, karaoke):
        resp = client.post(
            "/api/singer/favourite",
            data={"song": "/s/a.mp4", "starred": "true", "song_added_by": "   "},
        )
        assert resp.status_code == 422
        karaoke.singer_manager.add_favourite.assert_not_called()


class TestCurrentSingerName:
    def test_none_when_no_cookie(self, app):
        with app.test_request_context("/"):
            assert current_singer_name() is None

    def test_blank_cookie_is_none(self, app):
        with app.test_request_context("/", headers={"Cookie": "user=%20%20"}):
            assert current_singer_name() is None

    def test_a_plain_name(self, app):
        with app.test_request_context("/", headers={"Cookie": "user=Alice"}):
            assert current_singer_name() == "Alice"

    def test_a_non_ascii_name_round_trips(self, app):
        # js-cookie percent-encodes what it writes, so the server must decode it.
        with app.test_request_context("/", headers={"Cookie": "user=Bj%C3%B6rk"}):
            assert current_singer_name() == "Björk"
