"""Tests for history routes."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import werkzeug

# Monkeypatch werkzeug.__version__ for Flask compatibility if missing
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"


class TestHistoryRouteLogic:
    """Tests for history route logic using mocked template rendering."""

    @patch("pikaraoke.routes.history.render_template")
    @patch("pikaraoke.routes.history.is_admin", return_value=False)
    @patch("pikaraoke.routes.history.get_site_name", return_value="TestSite")
    @patch("pikaraoke.routes.history.get_karaoke_instance")
    @patch("pikaraoke.routes.history._", side_effect=lambda x: x)
    def test_history_calls_db_with_default_params(
        self, mock_gettext, mock_get_instance, mock_site_name, mock_is_admin, mock_render
    ):
        """Test that history route calls DB with correct default params."""
        from flask import Flask

        from pikaraoke.routes.history import history_bp

        mock_karaoke = MagicMock()
        mock_db = MagicMock()
        mock_db.get_last_plays.return_value = []
        mock_db.get_plays_count.return_value = 0
        mock_db.get_sessions_with_names.return_value = []
        mock_db.get_distinct_users.return_value = []
        mock_karaoke.db = mock_db
        mock_get_instance.return_value = mock_karaoke
        mock_render.return_value = "rendered"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(history_bp)

        with app.test_client() as client:
            response = client.get("/history")

        assert response.status_code == 200
        mock_db.get_last_plays.assert_called_once()
        call_kwargs = mock_db.get_last_plays.call_args[1]
        assert call_kwargs["limit"] == 20
        assert call_kwargs["offset"] == 0
        assert call_kwargs["sort_by"] == "timestamp"
        assert call_kwargs["sort_order"] == "DESC"

    @patch("pikaraoke.routes.history.render_template")
    @patch("pikaraoke.routes.history.is_admin", return_value=False)
    @patch("pikaraoke.routes.history.get_site_name", return_value="TestSite")
    @patch("pikaraoke.routes.history.get_karaoke_instance")
    @patch("pikaraoke.routes.history._", side_effect=lambda x: x)
    def test_history_with_date_filters(
        self, mock_gettext, mock_get_instance, mock_site_name, mock_is_admin, mock_render
    ):
        """Test that date filters are passed to DB."""
        from flask import Flask

        from pikaraoke.routes.history import history_bp

        mock_karaoke = MagicMock()
        mock_db = MagicMock()
        mock_db.get_last_plays.return_value = []
        mock_db.get_plays_count.return_value = 0
        mock_db.get_sessions_with_names.return_value = []
        mock_db.get_distinct_users.return_value = []
        mock_karaoke.db = mock_db
        mock_get_instance.return_value = mock_karaoke
        mock_render.return_value = "rendered"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(history_bp)

        with app.test_client() as client:
            client.get("/history?date_from=2025-01-01&date_to=2025-01-31")

        call_kwargs = mock_db.get_last_plays.call_args[1]
        assert call_kwargs["date_from"] == "2025-01-01"
        assert call_kwargs["date_to"] == "2025-01-31"

    @patch("pikaraoke.routes.history.render_template")
    @patch("pikaraoke.routes.history.is_admin", return_value=False)
    @patch("pikaraoke.routes.history.get_site_name", return_value="TestSite")
    @patch("pikaraoke.routes.history.get_karaoke_instance")
    @patch("pikaraoke.routes.history._", side_effect=lambda x: x)
    def test_history_with_user_filter(
        self, mock_gettext, mock_get_instance, mock_site_name, mock_is_admin, mock_render
    ):
        """Test that user filter is passed to DB."""
        from flask import Flask

        from pikaraoke.routes.history import history_bp

        mock_karaoke = MagicMock()
        mock_db = MagicMock()
        mock_db.get_last_plays.return_value = []
        mock_db.get_plays_count.return_value = 0
        mock_db.get_sessions_with_names.return_value = []
        mock_db.get_distinct_users.return_value = []
        mock_karaoke.db = mock_db
        mock_get_instance.return_value = mock_karaoke
        mock_render.return_value = "rendered"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(history_bp)

        with app.test_client() as client:
            client.get("/history?user=Alice")

        call_kwargs = mock_db.get_last_plays.call_args[1]
        assert call_kwargs["user_filter"] == "Alice"

    @patch("pikaraoke.routes.history.render_template")
    @patch("pikaraoke.routes.history.is_admin", return_value=False)
    @patch("pikaraoke.routes.history.get_site_name", return_value="TestSite")
    @patch("pikaraoke.routes.history.get_karaoke_instance")
    @patch("pikaraoke.routes.history._", side_effect=lambda x: x)
    def test_history_with_sessions_count(
        self, mock_gettext, mock_get_instance, mock_site_name, mock_is_admin, mock_render
    ):
        """Test that sessions_count param fetches last N sessions."""
        from flask import Flask

        from pikaraoke.routes.history import history_bp

        mock_karaoke = MagicMock()
        mock_db = MagicMock()
        mock_db.get_session_ids_last_n.return_value = ["sess-1", "sess-2", "sess-3"]
        mock_db.get_last_plays.return_value = []
        mock_db.get_plays_count.return_value = 0
        mock_db.get_sessions_with_names.return_value = []
        mock_db.get_distinct_users.return_value = []
        mock_karaoke.db = mock_db
        mock_get_instance.return_value = mock_karaoke
        mock_render.return_value = "rendered"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(history_bp)

        with app.test_client() as client:
            client.get("/history?sessions_count=3")

        mock_db.get_session_ids_last_n.assert_called_once_with(3)
        call_kwargs = mock_db.get_last_plays.call_args[1]
        assert call_kwargs["session_ids"] == ["sess-1", "sess-2", "sess-3"]

    @patch("pikaraoke.routes.history.render_template")
    @patch("pikaraoke.routes.history.is_admin", return_value=False)
    @patch("pikaraoke.routes.history.get_site_name", return_value="TestSite")
    @patch("pikaraoke.routes.history.get_karaoke_instance")
    @patch("pikaraoke.routes.history._", side_effect=lambda x: x)
    def test_history_with_sort_params(
        self, mock_gettext, mock_get_instance, mock_site_name, mock_is_admin, mock_render
    ):
        """Test that sort params are passed to DB."""
        from flask import Flask

        from pikaraoke.routes.history import history_bp

        mock_karaoke = MagicMock()
        mock_db = MagicMock()
        mock_db.get_last_plays.return_value = []
        mock_db.get_plays_count.return_value = 0
        mock_db.get_sessions_with_names.return_value = []
        mock_db.get_distinct_users.return_value = []
        mock_karaoke.db = mock_db
        mock_get_instance.return_value = mock_karaoke
        mock_render.return_value = "rendered"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(history_bp)

        with app.test_client() as client:
            client.get("/history?sort_by=song&sort_order=ASC")

        call_kwargs = mock_db.get_last_plays.call_args[1]
        assert call_kwargs["sort_by"] == "song"
        assert call_kwargs["sort_order"] == "ASC"

    @patch("pikaraoke.routes.history.render_template")
    @patch("pikaraoke.routes.history.flash")
    @patch("pikaraoke.routes.history.is_admin", return_value=False)
    @patch("pikaraoke.routes.history.get_site_name", return_value="TestSite")
    @patch("pikaraoke.routes.history.get_karaoke_instance")
    @patch("pikaraoke.routes.history._", side_effect=lambda x: x)
    def test_history_no_db(
        self, mock_gettext, mock_get_instance, mock_site_name, mock_is_admin, mock_flash, mock_render
    ):
        """Test that history handles missing DB gracefully."""
        from flask import Flask

        from pikaraoke.routes.history import history_bp

        mock_karaoke = MagicMock()
        mock_karaoke.db = None
        mock_get_instance.return_value = mock_karaoke
        mock_render.return_value = "rendered"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(history_bp)

        with app.test_client() as client:
            response = client.get("/history")

        assert response.status_code == 200
        mock_flash.assert_called_once()


class TestHistoryDatabaseMethods:
    """Tests for history-related database methods."""

    def test_get_last_plays_with_date_filter(self, temp_db_with_plays):
        """Test get_last_plays filters by date range."""
        db = temp_db_with_plays

        plays = db.get_last_plays(limit=100, date_from="2025-01-15", date_to="2025-01-16")

        # Should only include plays within the date range
        for play in plays:
            date = play["timestamp"][:10]
            assert date >= "2025-01-15"
            assert date <= "2025-01-16"

    def test_get_last_plays_with_user_filter(self, temp_db_with_plays):
        """Test get_last_plays filters by user."""
        db = temp_db_with_plays

        plays = db.get_last_plays(limit=100, user_filter="Alice")

        # Should only include plays by Alice
        for play in plays:
            assert play["canonical_name"] == "Alice"

    def test_get_last_plays_with_session_filter(self, temp_db_with_plays):
        """Test get_last_plays filters by session IDs."""
        db = temp_db_with_plays

        # Get sessions and filter by first one
        sessions = db.get_sessions_with_names()
        if sessions:
            session_id = sessions[0]["session_id"]
            plays = db.get_last_plays(limit=100, session_ids=[session_id])

            for play in plays:
                assert play["session_id"] == session_id

    def test_get_last_plays_sorting(self, temp_db_with_plays):
        """Test get_last_plays respects sort order."""
        db = temp_db_with_plays

        plays_desc = db.get_last_plays(limit=100, sort_by="timestamp", sort_order="DESC")
        plays_asc = db.get_last_plays(limit=100, sort_by="timestamp", sort_order="ASC")

        if len(plays_desc) > 1:
            assert plays_desc[0]["timestamp"] >= plays_desc[-1]["timestamp"]
        if len(plays_asc) > 1:
            assert plays_asc[0]["timestamp"] <= plays_asc[-1]["timestamp"]

    def test_get_plays_count(self, temp_db_with_plays):
        """Test get_plays_count returns correct count."""
        db = temp_db_with_plays

        total = db.get_plays_count()
        assert total > 0

        # Count with filter should be <= total
        filtered = db.get_plays_count(user_filter="Alice")
        assert filtered <= total

    def test_get_session_ids_last_n(self, temp_db_with_plays):
        """Test get_session_ids_last_n returns correct number of sessions."""
        db = temp_db_with_plays

        sessions = db.get_session_ids_last_n(2)
        assert len(sessions) <= 2
        assert all(isinstance(s, str) for s in sessions)

    def test_get_sessions_with_names(self, temp_db_with_plays):
        """Test get_sessions_with_names returns session data."""
        db = temp_db_with_plays

        sessions = db.get_sessions_with_names()
        assert isinstance(sessions, list)
        for session in sessions:
            assert "session_id" in session
            assert "name" in session

    def test_get_distinct_users(self, temp_db_with_plays):
        """Test get_distinct_users returns unique users."""
        db = temp_db_with_plays

        users = db.get_distinct_users()
        assert isinstance(users, list)
        assert len(users) == len(set(users))  # All unique


@pytest.fixture
def temp_db_with_plays():
    """Create a temporary database with sample plays for testing."""
    import os
    import sqlite3
    import tempfile

    from pikaraoke.lib.database import PlayDatabase

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = PlayDatabase(db_path)

    # Add sessions
    db.ensure_session("session-1", "2025-01-15")
    db.ensure_session("session-2", "2025-01-20")

    # Add some plays with specific timestamps
    with sqlite3.connect(db_path) as conn:
        plays = [
            ("2025-01-15 10:00:00", "session-1", "Alice", "Alice", "Song A", 1),
            ("2025-01-15 10:30:00", "session-1", "Bob", "Bob", "Song B", 1),
            ("2025-01-15 11:00:00", "session-1", "Alice", "Alice", "Song C", 0),
            ("2025-01-20 14:00:00", "session-2", "Charlie", "Charlie", "Song D", 1),
            ("2025-01-20 14:30:00", "session-2", "Alice", "Alice", "Song E", 1),
        ]
        for play in plays:
            conn.execute(
                "INSERT INTO users (canonical_name) VALUES (?) ON CONFLICT DO NOTHING",
                (play[2],),
            )
            conn.execute(
                """
                INSERT INTO plays (timestamp, session_id, canonical_name, display_name, song, completed)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                play,
            )
        conn.commit()

    yield db

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)
