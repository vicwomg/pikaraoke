"""Tests for admin authentication routes."""

import datetime
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_babel import Babel

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.auth import install_auth_gate, public
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.admin import admin_bp, delayed_halt
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


POWEROFF = ["systemctl", "poweroff"]
EXPAND = ["raspi-config", "--expand-rootfs"]
REBOOT = ["systemctl", "reboot"]


def _halt(commands, *, refuse=(), missing=False):
    """Run delayed_halt against a fake host, returning the Karaoke and every command run.

    `refuse` lists the attempts the host turns down; `missing` makes every binary absent.
    """
    k = MagicMock()
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        if missing:
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(cmd, 1 if cmd in refuse else 0, "", "denied")

    with (
        patch("pikaraoke.routes.admin.time.sleep"),
        patch("pikaraoke.routes.admin.subprocess.run", side_effect=run),
    ):
        delayed_halt(k, [("Halting", cmd) for cmd in commands], "refused")
    return k, ran


class TestDelayedHalt:
    """A halt runs where the host permits it, and says so where it does not."""

    def test_root_runs_the_command_directly(self):
        k, ran = _halt([POWEROFF])

        assert ran == [POWEROFF]
        k.stop.assert_called_once()

    def test_a_refused_command_falls_back_to_passwordless_sudo(self):
        k, ran = _halt([POWEROFF], refuse=[POWEROFF])

        assert ran == [POWEROFF, ["sudo", "-n", *POWEROFF]]
        k.stop.assert_called_once()

    def test_a_refusal_keeps_the_queue_and_tells_the_room(self):
        k, _ = _halt([POWEROFF], refuse=[POWEROFF, ["sudo", "-n", *POWEROFF]])

        k.queue_manager.queue_clear.assert_not_called()
        k.stop.assert_not_called()
        k.reset_now_playing_notification.assert_called_once()
        k.send_notification.assert_called_once_with("refused", "danger")

    def test_a_sudo_success_logs_what_ran_without_a_warning(self, caplog):
        """Stock Pi OS always refuses the direct attempt; that is not a fault."""
        caplog.set_level(logging.INFO)

        _halt([POWEROFF], refuse=[POWEROFF])

        [record] = caplog.records
        assert record.levelno == logging.INFO
        assert record.message == (
            "Halting: ran sudo -n systemctl poweroff, after systemctl poweroff: denied"
        )

    def test_a_refusal_logs_both_reasons_and_the_rule_once(self, caplog):
        _halt([POWEROFF], refuse=[POWEROFF, ["sudo", "-n", *POWEROFF]])

        [record] = caplog.records
        assert "systemctl poweroff: denied; sudo -n systemctl poweroff: denied" in record.message
        assert "NOPASSWD:" in record.message

    def test_a_missing_binary_is_a_refusal(self):
        k, _ = _halt([POWEROFF], missing=True)

        k.stop.assert_not_called()
        k.send_notification.assert_called_once_with("refused", "danger")

    def test_a_refused_expand_never_reboots(self):
        _, ran = _halt([EXPAND, REBOOT], refuse=[EXPAND, ["sudo", "-n", *EXPAND]])

        assert REBOOT not in ran

    def test_quit_runs_nothing_and_exits(self):
        with pytest.raises(SystemExit):
            _halt([])
