"""Tests for the singer-facing API and the identity-cookie helper."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_babel import Babel

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.auth import install_auth_gate, public
from pikaraoke.lib.current_app import current_singer_name
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.singer_manager import SINGER_NAME_MAX_LENGTH
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

    def test_a_name_over_the_cap_is_refused(self, client, karaoke):
        resp = client.post(
            "/api/singer/favourite",
            data={
                "song": "/s/a.mp4",
                "starred": "true",
                "song_added_by": "x" * (SINGER_NAME_MAX_LENGTH + 1),
            },
        )
        assert resp.status_code == 422
        karaoke.singer_manager.add_favourite.assert_not_called()

    def test_the_name_is_normalised_before_the_manager_sees_it(self, client, karaoke):
        client.post(
            "/api/singer/favourite",
            data={"song": "/s/a.mp4", "starred": "true", "song_added_by": "  Jo   B  "},
        )
        karaoke.singer_manager.add_favourite.assert_called_once_with("Jo B", "/s/a.mp4")


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


class TestRenameSinger:
    def _post(self, client, old_name, new_name, cookie=None):
        if cookie is not None:
            client.set_cookie("user", cookie)
        return client.post("/api/singer/rename", data={"old_name": old_name, "new_name": new_name})

    def test_a_free_rename_succeeds_and_reports_the_action(self, client, karaoke):
        karaoke.rename_singer.return_value = "renamed"
        resp = self._post(client, "Dvae", "Davey", cookie="Dvae")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body == {"success": True, "name": "Davey", "action": "renamed"}
        karaoke.rename_singer.assert_called_once_with("Dvae", "Davey")

    def test_adoption_also_succeeds(self, client, karaoke):
        karaoke.rename_singer.return_value = "adopt"
        resp = self._post(client, "Dvae", "Davey", cookie="Dvae")
        assert resp.status_code == 200
        assert resp.get_json()["action"] == "adopt"

    def test_a_taken_name_is_refused(self, client, karaoke):
        karaoke.rename_singer.return_value = "taken"
        resp = self._post(client, "Dvae", "Dave", cookie="Dvae")
        assert resp.status_code == 409
        assert resp.get_json()["success"] is False

    def test_a_reserved_name_is_refused(self, client, karaoke):
        karaoke.rename_singer.return_value = "reserved"
        resp = self._post(client, "Dvae", "Pikaraoke", cookie="Dvae")
        assert resp.status_code == 409

    def test_you_cannot_rename_a_name_you_are_not_holding(self, client, karaoke):
        resp = self._post(client, "Dvae", "Davey", cookie="Someone")
        assert resp.status_code == 403
        karaoke.rename_singer.assert_not_called()

    def test_a_nameless_device_cannot_rename(self, client, karaoke):
        resp = self._post(client, "Dvae", "Davey")
        assert resp.status_code == 403
        karaoke.rename_singer.assert_not_called()

    def test_a_blank_new_name_is_refused(self, client, karaoke):
        resp = self._post(client, "Dvae", "   ", cookie="Dvae")
        assert resp.status_code == 422
        karaoke.rename_singer.assert_not_called()

    def test_a_new_name_over_the_cap_is_refused(self, client, karaoke):
        resp = self._post(client, "Dvae", "x" * (SINGER_NAME_MAX_LENGTH + 1), cookie="Dvae")
        assert resp.status_code == 422
        karaoke.rename_singer.assert_not_called()

    def test_names_are_normalised_before_the_orchestrator_sees_them(self, client, karaoke):
        karaoke.rename_singer.return_value = "renamed"
        self._post(client, "Dvae", "  Dav   ey  ", cookie="Dvae")
        karaoke.rename_singer.assert_called_once_with("Dvae", "Dav ey")
