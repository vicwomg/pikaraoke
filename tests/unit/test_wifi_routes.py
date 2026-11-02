"""Tests for the Wi-Fi API routes: admin-only, absent off a supported Pi."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from pikaraoke.lib.auth import install_auth_gate
from pikaraoke.lib.wifi_manager import NetworkState
from pikaraoke.routes.wifi import wifi_bp
from pikaraoke.routes.wifi_api import wifi_api_bp
from tests.conftest import StubAdminAuth, make_route_app

ROUTE_PREFIX = "pikaraoke.routes.wifi_api"

# The page url_for()s the API endpoints, which its own blueprint does not define.
WIFI_API_LINKS = [
    ("/api/wifi/state", "wifi_api.wifi_state"),
    ("/api/wifi/networks", "wifi_api.wifi_networks"),
    ("/api/wifi/connect", "wifi_api.wifi_connect"),
]


def _app(admin: bool = True):
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin=admin)
    app.register_blueprint(wifi_api_bp)
    install_auth_gate(app)
    return app


@pytest.fixture
def client():
    return _app().test_client()


@pytest.fixture
def karaoke():
    """A mock Karaoke whose wifi_manager is supported and on its hotspot."""
    with patch(f"{ROUTE_PREFIX}.get_karaoke_instance") as get_instance:
        k = MagicMock()
        k.wifi_manager.supported = True
        k.wifi_manager.hotspot_name = "PiKaraoke-0000"
        k.wifi_manager.hotspot_password = "Catch22Horse"
        k.wifi_manager.state.return_value = NetworkState(kind="hotspot", network="PiKaraoke-0000")
        k.wifi_manager.networks.return_value = [
            {"name": "HomeNet", "signal": 70, "security": "WPA2"}
        ]
        get_instance.return_value = k
        yield k


class TestWhenSupported:
    def test_state_reports_the_hotspot(self, client, karaoke):
        body = client.get("/api/wifi/state").get_json()
        assert body["kind"] == "hotspot"
        assert body["hotspot_name"] == "PiKaraoke-0000"
        assert body["hotspot_password"] == "Catch22Horse"

    def test_networks_returns_the_scan(self, client, karaoke):
        body = client.get("/api/wifi/networks").get_json()
        assert body == [{"name": "HomeNet", "signal": 70, "security": "WPA2"}]

    def test_connect_answers_switching_and_schedules_the_attempt(self, client, karaoke):
        response = client.post("/api/wifi/connect", data={"name": "HomeNet", "password": "hunter2"})
        assert response.get_json() == {"status": "switching", "network": "HomeNet"}
        karaoke.start_wifi_connect.assert_called_once_with("HomeNet", "hunter2")
        # The route hands off to the background; it never joins inline.
        karaoke.wifi_manager.connect.assert_not_called()


class TestWhenNotSupported:
    @pytest.fixture
    def unsupported(self):
        with patch(f"{ROUTE_PREFIX}.get_karaoke_instance") as get_instance:
            k = MagicMock()
            k.wifi_manager.supported = False
            get_instance.return_value = k
            yield k

    def test_state_is_absent(self, client, unsupported):
        assert client.get("/api/wifi/state").status_code == 404

    def test_networks_is_absent(self, client, unsupported):
        assert client.get("/api/wifi/networks").status_code == 404

    def test_connect_is_absent(self, client, unsupported):
        response = client.post("/api/wifi/connect", data={"name": "HomeNet"})
        assert response.status_code == 404
        unsupported.start_wifi_connect.assert_not_called()


class TestAdminOnly:
    def test_endpoints_refuse_a_non_admin(self):
        client = _app(admin=False).test_client()
        assert client.get("/api/wifi/state").status_code == 403
        assert client.get("/api/wifi/networks").status_code == 403
        assert client.post("/api/wifi/connect", data={"name": "X"}).status_code == 403


class TestWifiPage:
    def test_renders_when_supported(self):
        app = make_route_app(wifi_bp, WIFI_API_LINKS, admin=True)
        k = MagicMock()
        k.wifi_manager.supported = True
        with (
            patch("pikaraoke.routes.wifi.get_karaoke_instance", return_value=k),
            patch("pikaraoke.routes.wifi.get_site_name", return_value="PiKaraoke"),
        ):
            response = app.test_client().get("/wifi")
        assert response.status_code == 200
        assert "wifi-page" in response.get_data(as_text=True)

    def test_is_absent_when_not_supported(self):
        app = make_route_app(wifi_bp, WIFI_API_LINKS, admin=True)
        k = MagicMock()
        k.wifi_manager.supported = False
        with patch("pikaraoke.routes.wifi.get_karaoke_instance", return_value=k):
            response = app.test_client().get("/wifi")
        assert response.status_code == 404

    def test_redirects_a_non_admin(self):
        app = make_route_app(wifi_bp, WIFI_API_LINKS, admin=False)
        response = app.test_client().get("/wifi")
        assert response.status_code == 302
