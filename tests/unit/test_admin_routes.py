"""Tests for admin authentication routes."""

import datetime

import pytest
from flask import Flask
from flask_babel import Babel

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.auth import install_auth_gate, public
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.auth_api import auth_api_bp
from pikaraoke.routes.library_api import library_bp

PASSWORD = "hunter2"


@pytest.fixture
def auth(tmp_path):
    store = AdminAuth(PreferenceManager(str(tmp_path / "config.ini")))
    store.set_password(PASSWORD)
    return store


@pytest.fixture
def app(auth):
    test_app = Flask(__name__)
    test_app.secret_key = auth.secret_key
    test_app.config["ADMIN_AUTH"] = auth
    test_app.config["SESSION_COOKIE_HTTPONLY"] = True
    test_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    test_app.permanent_session_lifetime = datetime.timedelta(days=90)
    Babel(test_app)
    test_app.register_blueprint(admin_bp)
    test_app.register_blueprint(auth_api_bp)
    test_app.register_blueprint(library_bp)
    test_app.add_url_rule("/info", "info.info", public(lambda: ""))
    test_app.add_url_rule("/", "home.home", public(lambda: ""))
    # Host-only and free of the Karaoke instance, so a test can watch the gate
    # open rather than a view succeed.
    test_app.add_url_rule("/api/gated", "gated", lambda: "")
    install_auth_gate(test_app)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, password=PASSWORD):
    return client.post("/auth", data={"admin_password": password})


class TestAdminCookieAttributes:
    """The session cookie carries the attributes that blunt cross-site requests."""

    def test_login_cookie_is_httponly_and_samesite_lax(self, client):
        header = _login(client).headers["Set-Cookie"]

        assert "HttpOnly" in header
        assert "SameSite=Lax" in header

    def test_login_cookie_is_not_secure(self, client):
        """PiKaraoke serves plain HTTP on a LAN; a Secure cookie would never come back."""
        assert "Secure" not in _login(client).headers["Set-Cookie"]

    def test_login_cookie_does_not_contain_the_password(self, client):
        assert PASSWORD not in _login(client).headers["Set-Cookie"]


class TestLogin:
    def test_correct_password_establishes_the_session(self, client, auth):
        _login(client)

        with client.session_transaction() as session:
            assert session["admin"] == auth.session_token

    def test_incorrect_password_does_not(self, client):
        _login(client, "wrong")

        with client.session_transaction() as session:
            assert "admin" not in session

    def test_logout_drops_the_session(self, client):
        _login(client)
        client.post("/logout")

        with client.session_transaction() as session:
            assert "admin" not in session


class TestSetAdminPassword:
    def test_admin_can_change_it_and_stays_logged_in(self, client, auth):
        _login(client)

        client.post("/admin_password", data={"admin_password": "new-one"})

        assert auth.verify("new-one")
        with client.session_transaction() as session:
            assert session["admin"] == auth.session_token

    def test_changing_it_logs_other_devices_out(self, client, app):
        other = app.test_client()
        _login(other)

        _login(client)
        client.post("/admin_password", data={"admin_password": "new-one"})

        assert other.get("/api/library_stats").status_code == 403

    def test_an_empty_password_clears_it(self, client, auth):
        _login(client)

        client.post("/admin_password", data={"admin_password": ""})

        assert not auth.is_password_set()

    def test_a_non_admin_cannot_change_it(self, client, auth):
        client.post("/admin_password", data={"admin_password": "new-one"})

        assert auth.verify(PASSWORD)


class TestApiLogin:
    """The JSON door, for a client that cannot read a flash message."""

    def test_correct_password_establishes_the_session(self, client, auth):
        response = client.post("/api/auth", json={"admin_password": PASSWORD})

        assert response.status_code == 200
        with client.session_transaction() as session:
            assert session["admin"] == auth.session_token

    def test_incorrect_password_is_a_401(self, client):
        response = client.post("/api/auth", json={"admin_password": "wrong"})

        assert response.status_code == 401
        with client.session_transaction() as session:
            assert "admin" not in session

    def test_the_cookie_it_issues_opens_a_host_only_route(self, client):
        client.post("/api/auth", json={"admin_password": PASSWORD})

        assert client.get("/api/gated").status_code == 200

    def test_status_tells_a_guest_a_password_is_wanted(self, client):
        assert client.get("/api/auth").get_json() == {
            "authenticated": False,
            "password_required": True,
        }

    def test_an_open_box_accepts_anyone(self, client, auth):
        auth.set_password(None)

        assert client.post("/api/auth", json={"admin_password": ""}).status_code == 200
