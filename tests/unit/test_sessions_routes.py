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


# Every host-only API endpoint, as (method, path). A new endpoint added without
# the gate should show up here rather than in production.
API_ENDPOINTS = [
    ("get", "/api/history/singers"),
    ("delete", "/api/history/plays/1"),
    ("get", "/api/history/sessions"),
    ("post", "/api/history/sessions"),
    ("put", "/api/history/sessions/abc"),
    ("delete", "/api/history/sessions/abc"),
    ("get", "/api/history/export/abc"),
    ("delete", "/api/history?scope=all"),
]


class TestAdminGate:
    @pytest.mark.parametrize("method,path", API_ENDPOINTS)
    def test_api_forbids_non_admin(self, client, method, path):
        response = getattr(client, method)(path)
        assert response.status_code == 403

    def test_sessions_page_redirects_non_admin(self, client):
        """Managing the night is the host's; reporting on it is not."""
        assert client.get("/sessions").status_code == 302

    def test_singers_allows_admin(self, admin_client, karaoke):
        karaoke.play_history.get_singers.return_value = [{"performer": "Alice", "play_count": 2}]

        response = admin_client.get("/api/history/singers")

        assert response.status_code == 200
        assert json.loads(response.data)["singers"][0]["performer"] == "Alice"


class TestPublicPlayLog:
    """The play log is the one part of this feature the whole room may read: a
    guest looking up what they sang last time and queuing it again is the point
    of the page. Everything around it stays with the host."""

    def test_guests_can_read_the_log(self, client, karaoke):
        karaoke.play_history.get_plays.return_value = [{"id": 1, "song": "A Song"}]
        karaoke.play_history.count_plays.return_value = 1

        response = client.get("/api/history/plays")

        assert response.status_code == 200
        assert json.loads(response.data)["plays"][0]["song"] == "A Song"

    @pytest.mark.parametrize("path", ["/history", "/rankings"])
    def test_guests_can_open_the_reporting_pages(self, client, karaoke_page, path):
        karaoke_page.play_history.get_sessions.return_value = []

        with patch("pikaraoke.routes.sessions.render_template", return_value="ok"):
            assert client.get(path).status_code == 200

    def test_guests_cannot_delete_an_entry(self, client, karaoke):
        response = client.delete("/api/history/plays/1")

        assert response.status_code == 403
        karaoke.play_history.delete_play.assert_not_called()

    def test_the_page_says_whether_deleting_is_offered(self, admin_client, client, karaoke_page):
        """The menu only shows Delete to the host; the API refuses it either way."""
        karaoke_page.play_history.get_sessions.return_value = []

        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            admin_client.get("/history")
            assert render.call_args.kwargs["admin"] is True

            client.get("/history")
            assert render.call_args.kwargs["admin"] is False

    def test_the_page_carries_the_session_filter(self, client, karaoke_page):
        """The filter is a link, so the uuid arrives in the query string."""
        karaoke_page.play_history.get_sessions.return_value = [{"uuid": "abc", "name": "Fri"}]

        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            client.get("/history?session=abc")

        assert render.call_args.kwargs["selected_session"] == "abc"
        assert render.call_args.kwargs["sessions"][0]["name"] == "Fri"

    def test_the_page_carries_the_performer_filter(self, client, karaoke_page):
        """Reached from a name on the rankings or in a session's singer list."""
        karaoke_page.play_history.get_sessions.return_value = []

        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            client.get("/history?performer=Alice")

        assert render.call_args.kwargs["selected_performer"] == "Alice"

    def test_the_log_filters_by_performer(self, client, karaoke):
        """The rows and the count take the same filter, or the pager offers a
        page the log cannot fill."""
        karaoke.play_history.get_plays.return_value = []
        karaoke.play_history.count_plays.return_value = 0

        assert client.get("/api/history/plays?performer=Alice").status_code == 200

        assert karaoke.play_history.get_plays.call_args.kwargs["performer"] == "Alice"
        assert karaoke.play_history.count_plays.call_args.kwargs["performer"] == "Alice"

    def test_the_log_filters_by_song(self, client, karaoke):
        """The id rides along with the title and is what decides the match, so a
        log opened from a chart row holds the plays that row counted."""
        karaoke.play_history.get_plays.return_value = []
        karaoke.play_history.count_plays.return_value = 0

        response = client.get("/api/history/plays?song=A+Song&youtube_id=dQw4w9WgXcQ")

        assert response.status_code == 200
        for call in (karaoke.play_history.get_plays, karaoke.play_history.count_plays):
            assert call.call_args.kwargs["song"] == "A Song"
            assert call.call_args.kwargs["youtube_id"] == "dQw4w9WgXcQ"

    def test_the_page_carries_the_song_filter(self, client, karaoke_page):
        karaoke_page.play_history.get_sessions.return_value = []

        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            client.get("/history?song=A+Song&youtube_id=dQw4w9WgXcQ")

        assert render.call_args.kwargs["selected_song"] == "A Song"
        assert render.call_args.kwargs["selected_youtube_id"] == "dQw4w9WgXcQ"


# What export_plays() hands the exporters: performances only, so there is no
# status to render.
EXPORTED_PLAYS = [
    {"played_at": "2026-03-05 21:00:00", "performer": "Alice", "song": "Artist - Song"},
    {"played_at": "2026-03-05 21:05:00", "performer": "Bob", "song": "Another Song"},
]


class TestExport:
    def test_csv_contents(self, admin_client, karaoke):
        karaoke.play_history.export_plays.return_value = EXPORTED_PLAYS

        response = admin_client.get("/api/history/export/abc")
        body = response.data.decode()

        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert "attachment" in response.headers["Content-Disposition"]
        # No status column: export_plays() returns songs sung through, so it
        # would read "Played" the whole way down.
        assert "Played At,Performer,Song" in body
        assert "Status" not in body
        assert "2026-03-05 21:00:00,Alice,Artist - Song" in body
        assert "2026-03-05 21:05:00,Bob,Another Song" in body

    def test_txt_contents(self, admin_client, karaoke):
        karaoke.play_history.export_plays.return_value = EXPORTED_PLAYS

        response = admin_client.get("/api/history/export/abc?format=txt")
        body = response.data.decode()

        assert response.status_code == 200
        assert response.mimetype == "text/plain"
        assert 'filename="pikaraoke-abc.txt"' in response.headers["Content-Disposition"]
        # A numbered, human-readable set list: minutes only, no CSV commas.
        assert "1. 2026-03-05 21:00  Alice - Artist - Song" in body
        assert "2. 2026-03-05 21:05  Bob - Another Song" in body
        # Nothing is flagged: a skipped song never reaches the export.
        assert "skipped" not in body

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_csv_neutralises_formula_prefixes(self, admin_client, karaoke, prefix):
        """A queued name must not execute when the host opens the export.

        song_added_by is free text from any device on the network, so a
        performer called '=HYPERLINK(...)' would otherwise run as a formula in
        Excel and LibreOffice.
        """
        karaoke.play_history.export_plays.return_value = [
            {
                "played_at": "2026-03-05 21:00:00",
                "performer": f'{prefix}HYPERLINK("http://evil/","Alice")',
                "song": f"{prefix}cmd|'/c calc'!A1",
            }
        ]

        body = admin_client.get("/api/history/export/abc").data.decode()

        # Quoted by the csv writer, so the apostrophe is the first cell character.
        assert f"\"'{prefix}HYPERLINK" in body
        assert f"'{prefix}cmd|" in body

    def test_csv_leaves_ordinary_names_alone(self, admin_client, karaoke):
        karaoke.play_history.export_plays.return_value = EXPORTED_PLAYS[:1]

        body = admin_client.get("/api/history/export/abc").data.decode()

        assert "2026-03-05 21:00:00,Alice,Artist - Song" in body

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
        assert "Played At,Performer,Song" in response.data.decode()


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

    def test_sessions_show_auto_started_by_default(self, admin_client, karaoke):
        karaoke.play_history.get_sessions.return_value = []
        karaoke.play_history.count_sessions.return_value = 0
        karaoke.play_history.get_current_session.return_value = None

        assert admin_client.get("/api/history/sessions").status_code == 200
        karaoke.play_history.get_sessions.assert_called_once_with(50, 0, True)
        karaoke.play_history.count_sessions.assert_called_once_with(True)

    def test_sessions_can_hide_auto_started(self, admin_client, karaoke):
        """The total has to take the same filter, or the pager overruns the list."""
        karaoke.play_history.get_sessions.return_value = []
        karaoke.play_history.count_sessions.return_value = 0
        karaoke.play_history.get_current_session.return_value = None

        assert admin_client.get("/api/history/sessions?include_unnamed=false").status_code == 200
        karaoke.play_history.get_sessions.assert_called_once_with(50, 0, False)
        karaoke.play_history.count_sessions.assert_called_once_with(False)

    def test_singers_defaults_to_no_cap(self, admin_client, karaoke):
        """The session singer panel wants everyone who sang, bounded by the session."""
        karaoke.play_history.get_singers.return_value = []

        assert admin_client.get("/api/history/singers?session=abc").status_code == 200
        karaoke.play_history.get_singers.assert_called_once_with("abc", None, False)


class TestRankingsSizes:
    """The rankings lists are top-N, so a row-count selector stands in for paging."""

    def test_honors_selected_sizes(self, admin_client, karaoke_page):
        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            response = admin_client.get("/rankings?songs=50&performers=10")

        assert response.status_code == 200
        karaoke_page.play_history.get_top_songs.assert_called_once_with(50, None)
        karaoke_page.play_history.get_singers.assert_called_once_with(
            None, limit=10, completed_only=True
        )
        # The chosen sizes are handed to the template so the dropdowns show them.
        kwargs = render.call_args.kwargs
        assert kwargs["limits"]["songs"] == 50
        assert kwargs["limits"]["performers"] == 10

    def test_defaults_when_unset(self, admin_client, karaoke_page):
        with patch("pikaraoke.routes.sessions.render_template", return_value="ok"):
            admin_client.get("/rankings")

        karaoke_page.play_history.get_top_songs.assert_called_once_with(20, None)
        karaoke_page.play_history.get_singers.assert_called_once_with(
            None, limit=20, completed_only=True
        )

    def test_off_menu_size_rejected(self, admin_client, karaoke_page):
        # Only the offered sizes are accepted, so a hand-edited URL cannot ask
        # for an unbounded list.
        response = admin_client.get("/rankings?songs=999")
        assert response.status_code == 422


class TestRankingsSessionFilter:
    """Rankings cover every play on record, or one night, chosen from the same
    dropdown the play log uses."""

    def test_every_session_is_the_default(self, admin_client, karaoke_page):
        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            admin_client.get("/rankings")

        assert render.call_args.kwargs["selected_session"] == ""
        karaoke_page.play_history.get_top_songs.assert_called_once_with(20, None)
        karaoke_page.play_history.get_singers.assert_called_once_with(
            None, limit=20, completed_only=True
        )

    def test_a_session_filters_both_charts(self, admin_client, karaoke_page):
        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            admin_client.get("/rankings?session=abc")

        karaoke_page.play_history.get_top_songs.assert_called_once_with(20, "abc")
        karaoke_page.play_history.get_singers.assert_called_once_with(
            "abc", limit=20, completed_only=True
        )
        assert render.call_args.kwargs["selected_session"] == "abc"

    def test_the_dropdown_is_offered_every_session(self, admin_client, karaoke_page):
        karaoke_page.play_history.get_sessions.return_value = [{"uuid": "abc", "name": "Fri"}]

        with patch("pikaraoke.routes.sessions.render_template", return_value="ok") as render:
            admin_client.get("/rankings")

        assert render.call_args.kwargs["sessions"][0]["name"] == "Fri"


class TestResetHistory:
    """Wiping the log is irreversible, so the scope is never inferred."""

    def test_clears_one_session(self, admin_client, karaoke):
        karaoke.play_history.clear_session_plays.return_value = True

        response = admin_client.delete("/api/history?scope=session&session=abc")

        assert response.status_code == 200
        karaoke.play_history.clear_session_plays.assert_called_once_with("abc")
        karaoke.play_history.clear_all_history.assert_not_called()

    def test_clears_everything(self, admin_client, karaoke):
        response = admin_client.delete("/api/history?scope=all")

        assert response.status_code == 200
        karaoke.play_history.clear_all_history.assert_called_once_with()
        karaoke.play_history.clear_session_plays.assert_not_called()

    def test_unknown_session_is_not_found(self, admin_client, karaoke):
        karaoke.play_history.clear_session_plays.return_value = False

        response = admin_client.delete("/api/history?scope=session&session=gone")

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "query",
        [
            "",  # no scope at all must not fall through to wiping everything
            "?scope=everything",
            "?scope=session",  # scoped, but to no session in particular
            "?session=abc",
        ],
    )
    def test_ambiguous_request_destroys_nothing(self, admin_client, karaoke, query):
        response = admin_client.delete("/api/history" + query)

        assert response.status_code == 422
        karaoke.play_history.clear_all_history.assert_not_called()
        karaoke.play_history.clear_session_plays.assert_not_called()
