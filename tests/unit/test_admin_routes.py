"""Tests for admin authentication routes."""

from unittest.mock import patch

import pytest
import werkzeug
from flask import Flask
from flask_babel import Babel

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.admin import admin_bp

ROUTE_PREFIX = "pikaraoke.routes.admin"


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.secret_key = "test"
    Babel(test_app)
    test_app.register_blueprint(admin_bp)
    test_app.add_url_rule("/info", "info.info", lambda: "")
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _set_cookie_header(response):
    return response.headers["Set-Cookie"]


class TestAdminCookieAttributes:
    """The admin cookie carries the attributes that blunt cross-site requests."""

    def test_login_cookie_is_httponly_and_samesite_lax(self, client):
        with patch(f"{ROUTE_PREFIX}.get_admin_password", return_value="hunter2"):
            response = client.post("/auth", data={"admin_password": "hunter2", "next": "/"})

        header = _set_cookie_header(response)
        assert "HttpOnly" in header
        assert "SameSite=Lax" in header

    def test_login_cookie_is_not_secure(self, client):
        """PiKaraoke serves plain HTTP on a LAN; a Secure cookie would never come back."""
        with patch(f"{ROUTE_PREFIX}.get_admin_password", return_value="hunter2"):
            response = client.post("/auth", data={"admin_password": "hunter2", "next": "/"})

        assert "Secure" not in _set_cookie_header(response)

    def test_logout_cookie_matches_the_login_attributes(self, client):
        with patch(f"{ROUTE_PREFIX}.get_admin_password", return_value="hunter2"):
            response = client.get("/logout")

        header = _set_cookie_header(response)
        assert "HttpOnly" in header
        assert "SameSite=Lax" in header
