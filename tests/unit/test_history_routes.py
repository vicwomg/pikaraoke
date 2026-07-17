"""Tests for the play history routes, focused on the admin gate.

/api/history/singers is effectively the guest list for the event, so every
endpoint in these blueprints must be closed to non-admins.
"""

import json

import pytest
import werkzeug
from flask import Flask

# Monkeypatch werkzeug.__version__ for Flask compatibility if missing
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from unittest.mock import MagicMock, patch

from flask_babel import Babel

from pikaraoke.routes.history import history_bp
from pikaraoke.routes.history_api import history_api_bp

ADMIN_PASSWORD = "secret"


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.secret_key = "test"
    test_app.config["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    test_app.config["SITE_NAME"] = "PiKaraoke"
    Babel(test_app)
    test_app.register_blueprint(history_api_bp)
    test_app.register_blueprint(history_bp)

    # The non-admin redirect target; the real app supplies this via home_bp.
    test_app.add_url_rule("/", endpoint="home.home", view_func=lambda: "home")

    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    c = app.test_client()
    c.set_cookie("admin", ADMIN_PASSWORD)
    return c


@pytest.fixture
def karaoke():
    with patch("pikaraoke.routes.history_api.get_karaoke_instance") as get_instance:
        k = MagicMock()
        get_instance.return_value = k
        yield k


# Every API endpoint, as (method, path). A new endpoint added without the gate
# should show up here rather than in production.
API_ENDPOINTS = [
    ("get", "/api/history/singers"),
    ("get", "/api/history/plays"),
    ("delete", "/api/history/plays/1"),
    ("get", "/api/history/sessions"),
    ("post", "/api/history/sessions"),
    ("put", "/api/history/sessions/abc"),
    ("delete", "/api/history/sessions/abc"),
    ("get", "/api/history/export/abc"),
]


class TestAdminGate:
    @pytest.mark.parametrize("method,path", API_ENDPOINTS)
    def test_api_forbids_non_admin(self, client, method, path):
        response = getattr(client, method)(path)
        assert response.status_code == 403

    @pytest.mark.parametrize("path", ["/history", "/rankings"])
    def test_pages_redirect_non_admin(self, client, path):
        response = client.get(path)
        assert response.status_code == 302

    def test_singers_allows_admin(self, admin_client, karaoke):
        karaoke.play_history.get_singers.return_value = [{"performer": "Alice", "play_count": 2}]

        response = admin_client.get("/api/history/singers")

        assert response.status_code == 200
        assert json.loads(response.data)["singers"][0]["performer"] == "Alice"


class TestExport:
    def test_csv_contents(self, admin_client, karaoke):
        karaoke.play_history.export_plays.return_value = [
            {
                "played_at": "2026-03-05 21:00:00",
                "performer": "Alice",
                "completed": 1,
                "file_path": "/songs/Artist - Song---abc12345678.mp4",
                "artist": None,
                "title": None,
            },
            {
                "played_at": "2026-03-05 21:05:00",
                "performer": "Bob",
                "completed": 0,
                "file_path": None,
                "artist": None,
                "title": None,
            },
        ]
        karaoke.song_manager.display_name_from_path.return_value = "Artist - Song"

        response = admin_client.get("/api/history/export/abc")
        body = response.data.decode()

        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert "attachment" in response.headers["Content-Disposition"]
        # Same vocabulary as the play log on the page, not a separate one.
        assert "Played At,Performer,Song,Status" in body
        assert "2026-03-05 21:00:00,Alice,Artist - Song,Played" in body
        # A song deleted from the library leaves the play, but with no title
        assert "2026-03-05 21:05:00,Bob,(song removed from library),Skipped" in body
