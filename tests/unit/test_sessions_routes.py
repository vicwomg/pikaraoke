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

from pikaraoke.lib.play_history_manager import SESSION_NAME_MAX_LENGTH
from pikaraoke.routes.sessions import sessions_bp
from pikaraoke.routes.sessions_api import sessions_api_bp

ADMIN_PASSWORD = "secret"


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.secret_key = "test"
    test_app.config["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    test_app.config["SITE_NAME"] = "PiKaraoke"
    Babel(test_app)
    test_app.register_blueprint(sessions_api_bp)
    test_app.register_blueprint(sessions_bp)

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
    with patch("pikaraoke.routes.sessions_api.get_karaoke_instance") as get_instance:
        k = MagicMock()
        get_instance.return_value = k
        yield k


@pytest.fixture
def karaoke_page():
    """Patch the karaoke instance for the page blueprint (history), not the api."""
    with patch("pikaraoke.routes.sessions.get_karaoke_instance") as get_instance:
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

    @pytest.mark.parametrize("path", ["/sessions", "/rankings"])
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
                "song": "Artist - Song",
            },
            {
                "played_at": "2026-03-05 21:05:00",
                "performer": "Bob",
                "completed": 0,
                "song": "Another Song",
            },
        ]

        response = admin_client.get("/api/history/export/abc")
        body = response.data.decode()

        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert "attachment" in response.headers["Content-Disposition"]
        # Same vocabulary as the play log on the page, not a separate one.
        assert "Played At,Performer,Song,Status" in body
        assert "2026-03-05 21:00:00,Alice,Artist - Song,Played" in body
        assert "2026-03-05 21:05:00,Bob,Another Song,Skipped" in body

    def test_txt_contents(self, admin_client, karaoke):
        karaoke.play_history.export_plays.return_value = [
            {
                "played_at": "2026-03-05 21:00:00",
                "performer": "Alice",
                "completed": 1,
                "song": "Artist - Song",
            },
            {
                "played_at": "2026-03-05 21:05:00",
                "performer": "Bob",
                "completed": 0,
                "song": "Another Song",
            },
        ]

        response = admin_client.get("/api/history/export/abc?format=txt")
        body = response.data.decode()

        assert response.status_code == 200
        assert response.mimetype == "text/plain"
        assert 'filename="pikaraoke-abc.txt"' in response.headers["Content-Disposition"]
        # A numbered, human-readable set list: minutes only, no CSV commas.
        assert "1. 2026-03-05 21:00  Alice - Artist - Song" in body
        # A skipped song is still listed, but flagged.
        assert "2. 2026-03-05 21:05  Bob - Another Song  (skipped)" in body

    def test_bad_format_rejected(self, admin_client, karaoke):
        karaoke.play_history.export_plays.return_value = []
        response = admin_client.get("/api/history/export/abc?format=xml")
        assert response.status_code == 422

    def test_unknown_session_is_404(self, admin_client, karaoke):
        """A deleted session must not download as an empty file, which reads as
        'nobody sang' rather than 'that session is gone'."""
        karaoke.play_history.session_exists.return_value = False

        response = admin_client.get("/api/history/export/gone")

        assert response.status_code == 404
        karaoke.play_history.export_plays.assert_not_called()

    def test_session_with_no_plays_still_exports(self, admin_client, karaoke):
        karaoke.play_history.session_exists.return_value = True
        karaoke.play_history.export_plays.return_value = []

        response = admin_client.get("/api/history/export/quiet")

        assert response.status_code == 200
        assert "Played At,Performer,Song,Status" in response.data.decode()


class TestSessionName:
    """A session name is a display value: the splash screen shows it across a TV
    and the nav ribbon carries it on every page."""

    def _start(self, client, name):
        return client.post(
            "/api/history/sessions",
            data=json.dumps({"name": name}),
            content_type="application/json",
        )

    def test_name_is_required(self, admin_client, karaoke):
        assert self._start(admin_client, "   ").status_code == 422
        karaoke.play_history.start_session.assert_not_called()

    def test_over_long_name_rejected(self, admin_client, karaoke):
        assert self._start(admin_client, "x" * (SESSION_NAME_MAX_LENGTH + 1)).status_code == 422
        karaoke.play_history.start_session.assert_not_called()

    def test_name_at_the_cap_accepted(self, admin_client, karaoke):
        karaoke.play_history.start_session.return_value = "session-uuid"
        name = "x" * SESSION_NAME_MAX_LENGTH

        assert self._start(admin_client, name).status_code == 200
        karaoke.play_history.start_session.assert_called_once_with(name)

    def test_over_long_rename_rejected(self, admin_client, karaoke):
        response = admin_client.put(
            "/api/history/sessions/abc",
            data=json.dumps(
                {"action": "rename", "name": "x" * (SESSION_NAME_MAX_LENGTH + 1)},
            ),
            content_type="application/json",
        )

        assert response.status_code == 422
        karaoke.play_history.rename_session.assert_not_called()

    @pytest.mark.parametrize("name", ["", "   ", "\t"])
    def test_blank_rename_rejected(self, admin_client, karaoke, name):
        response = admin_client.put(
            "/api/history/sessions/abc",
            data=json.dumps({"action": "rename", "name": name}),
            content_type="application/json",
        )

        assert response.status_code == 422
        karaoke.play_history.rename_session.assert_not_called()


class TestPagingBounds:
    """SQLite reads a negative LIMIT as no limit, so an unvalidated one would
    load the whole table on a Pi that is transcoding at the same time."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/history/plays?limit=-1",
            "/api/history/plays?limit=0",
            "/api/history/plays?limit=501",
            "/api/history/plays?offset=-1",
            "/api/history/sessions?limit=-1",
            "/api/history/sessions?offset=-5",
            "/api/history/singers?limit=-1",
        ],
    )
    def test_out_of_range_paging_rejected(self, admin_client, karaoke, path):
        response = admin_client.get(path)

        assert response.status_code == 422
        karaoke.play_history.get_plays.assert_not_called()
        karaoke.play_history.get_sessions.assert_not_called()
        karaoke.play_history.get_singers.assert_not_called()

    def test_singers_defaults_to_no_cap(self, admin_client, karaoke):
        """The session singer panel wants everyone who sang, bounded by the session."""
        karaoke.play_history.get_singers.return_value = []

        assert admin_client.get("/api/history/singers?session=abc").status_code == 200
        karaoke.play_history.get_singers.assert_called_once_with("abc", None)


class TestRankingsSizes:
    """The rankings lists are top-N, so a row-count selector stands in for paging."""

    def test_honors_selected_sizes(self, admin_client, karaoke_page):
        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            response = admin_client.get("/rankings?songs=50&performers=10&sessions=20")

        assert response.status_code == 200
        karaoke_page.play_history.get_top_songs.assert_called_once_with(50)
        karaoke_page.play_history.get_singers.assert_called_once_with(limit=10)
        karaoke_page.play_history.get_sessions.assert_called_once_with(limit=20)
        # The chosen sizes are handed to the template so the dropdowns show them.
        kwargs = render.call_args.kwargs
        assert kwargs["limits"]["songs"] == 50
        assert kwargs["limits"]["performers"] == 10
        assert kwargs["limits"]["sessions"] == 20

    def test_defaults_when_unset(self, admin_client, karaoke_page):
        with patch("pikaraoke.routes.sessions.render_template", return_value="ok"):
            admin_client.get("/rankings")

        karaoke_page.play_history.get_top_songs.assert_called_once_with(20)
        karaoke_page.play_history.get_singers.assert_called_once_with(limit=20)
        karaoke_page.play_history.get_sessions.assert_called_once_with(limit=10)

    def test_off_menu_size_rejected(self, admin_client, karaoke_page):
        # Only the offered sizes are accepted, so a hand-edited URL cannot ask
        # for an unbounded list.
        response = admin_client.get("/rankings?songs=999")
        assert response.status_code == 422
