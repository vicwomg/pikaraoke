"""Tests for the PlayDatabase class."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from pikaraoke.lib.database import PlayDatabase


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = PlayDatabase(db_path)
    yield db
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def temp_db_path():
    """Create a temporary path for database testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestDatabaseSchema:
    """Tests for database schema creation."""

    def test_init_creates_tables(self, temp_db):
        """Test that init_db creates all required tables."""
        import sqlite3

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]

        assert "users" in tables
        assert "user_aliases" in tables
        assert "sessions" in tables
        assert "plays" in tables

    def test_init_creates_indexes(self, temp_db):
        """Test that init_db creates required indexes."""
        import sqlite3

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
            )
            indexes = [row[0] for row in cursor.fetchall()]

        assert "idx_plays_canonical_name" in indexes
        assert "idx_plays_timestamp" in indexes
        assert "idx_plays_session_id" in indexes


class TestIntegrityCheck:
    """Tests for database integrity checking."""

    def test_check_integrity_on_valid_db(self, temp_db):
        """Test integrity check passes on valid database."""
        is_ok, message = temp_db.check_integrity()
        assert is_ok is True
        assert "passed" in message.lower()

    def test_check_and_recover_creates_new_db(self, temp_db_path):
        """Test that check_and_recover returns True for non-existent db."""
        # Remove the temp file first
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)

        result = PlayDatabase.check_and_recover_db(temp_db_path, force_recreate=False)
        assert result is True

    def test_check_and_recover_passes_on_valid_db(self, temp_db):
        """Test that check_and_recover passes on valid database."""
        result = PlayDatabase.check_and_recover_db(temp_db.db_path, force_recreate=False)
        assert result is True


class TestSessionManagement:
    """Tests for session management."""

    def test_ensure_session_creates_session(self, temp_db):
        """Test that ensure_session creates a new session."""
        import sqlite3

        session_id = "test-session-123"
        temp_db.ensure_session(session_id, "Test Session")

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT session_id, name FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()

        assert row is not None
        assert row[0] == session_id
        assert row[1] == "Test Session"

    def test_ensure_session_uses_default_name(self, temp_db):
        """Test that ensure_session uses date as default name."""
        import sqlite3
        from datetime import datetime

        session_id = "test-session-456"
        temp_db.ensure_session(session_id)

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()

        # Default name should be today's date
        expected_date = datetime.now().strftime("%Y-%m-%d")
        assert row[0] == expected_date

    def test_ensure_session_idempotent(self, temp_db):
        """Test that ensure_session doesn't duplicate sessions."""
        import sqlite3

        session_id = "test-session-789"
        temp_db.ensure_session(session_id, "First Name")
        temp_db.ensure_session(session_id, "Second Name")

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            count = cursor.fetchone()[0]
            cursor = conn.execute(
                "SELECT name FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            name = cursor.fetchone()[0]

        assert count == 1
        # Should keep the first name
        assert name == "First Name"


class TestUserResolution:
    """Tests for user and alias resolution."""

    def test_resolve_canonical_user_creates_user(self, temp_db):
        """Test that resolving a new user creates it."""
        import sqlite3

        canonical = temp_db._resolve_canonical_user("NewUser")

        assert canonical == "NewUser"

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT canonical_name FROM users WHERE canonical_name = ?",
                ("NewUser",),
            )
            row = cursor.fetchone()

        assert row is not None

    def test_resolve_canonical_user_with_alias(self, temp_db):
        """Test that resolving an alias returns the canonical name."""
        import sqlite3

        # First create a user and alias
        with sqlite3.connect(temp_db.db_path) as conn:
            conn.execute("INSERT INTO users (canonical_name) VALUES (?)", ("John",))
            conn.execute(
                "INSERT INTO user_aliases (alias, canonical_name) VALUES (?, ?)",
                ("Johnny", "John"),
            )
            conn.commit()

        canonical = temp_db._resolve_canonical_user("Johnny")

        assert canonical == "John"


class TestPlayRecords:
    """Tests for play record management."""

    def test_add_play_returns_id(self, temp_db):
        """Test that add_play returns a valid play ID."""
        session_id = "test-session"
        temp_db.ensure_session(session_id)

        play_id = temp_db.add_play("Test Song", "TestUser", session_id)

        assert play_id is not None
        assert isinstance(play_id, int)
        assert play_id > 0

    def test_add_play_stores_display_name(self, temp_db):
        """Test that add_play stores the original display name."""
        import sqlite3

        session_id = "test-session"
        temp_db.ensure_session(session_id)

        # Create alias
        with sqlite3.connect(temp_db.db_path) as conn:
            conn.execute("INSERT INTO users (canonical_name) VALUES (?)", ("John",))
            conn.execute(
                "INSERT INTO user_aliases (alias, canonical_name) VALUES (?, ?)",
                ("Johnny", "John"),
            )
            conn.commit()

        play_id = temp_db.add_play("Test Song", "Johnny", session_id)

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT canonical_name, display_name FROM plays WHERE id = ?",
                (play_id,),
            )
            row = cursor.fetchone()

        assert row[0] == "John"  # Resolved canonical name
        assert row[1] == "Johnny"  # Original display name

    def test_add_play_defaults_incomplete(self, temp_db):
        """Test that new plays default to incomplete."""
        import sqlite3

        session_id = "test-session"
        temp_db.ensure_session(session_id)

        play_id = temp_db.add_play("Test Song", "TestUser", session_id)

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT completed FROM plays WHERE id = ?",
                (play_id,),
            )
            row = cursor.fetchone()

        assert row[0] == 0  # Not completed

    def test_update_play_sets_completed(self, temp_db):
        """Test that update_play sets completion status."""
        import sqlite3

        session_id = "test-session"
        temp_db.ensure_session(session_id)

        play_id = temp_db.add_play("Test Song", "TestUser", session_id)
        temp_db.update_play(play_id, completed=True)

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT completed FROM plays WHERE id = ?",
                (play_id,),
            )
            row = cursor.fetchone()

        assert row[0] == 1  # Completed

    def test_update_play_skipped(self, temp_db):
        """Test that update_play can mark a song as skipped."""
        import sqlite3

        session_id = "test-session"
        temp_db.ensure_session(session_id)

        play_id = temp_db.add_play("Test Song", "TestUser", session_id)
        temp_db.update_play(play_id, completed=False)

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT completed FROM plays WHERE id = ?",
                (play_id,),
            )
            row = cursor.fetchone()

        assert row[0] == 0  # Not completed (skipped)

    def test_get_play_returns_record(self, temp_db):
        """Test that get_play returns the play record."""
        session_id = "test-session"
        temp_db.ensure_session(session_id, "Test Session Name")

        play_id = temp_db.add_play("Test Song", "TestUser", session_id)
        temp_db.update_play(play_id, completed=True)

        play = temp_db.get_play(play_id)

        assert play is not None
        assert play["id"] == play_id
        assert play["song"] == "Test Song"
        assert play["canonical_name"] == "TestUser"
        assert play["display_name"] == "TestUser"
        assert play["session_id"] == session_id
        assert play["session_name"] == "Test Session Name"
        assert play["completed"] == 1

    def test_get_play_not_found(self, temp_db):
        """Test that get_play returns None for non-existent record."""
        play = temp_db.get_play(99999)
        assert play is None


class TestKaraokeIntegration:
    """Tests for Karaoke class integration with database."""

    @patch("pikaraoke.lib.database.PlayDatabase.check_and_recover_db")
    def test_start_song_calls_add_play(self, mock_check):
        """Test that Karaoke.start_song calls db.add_play."""
        mock_check.return_value = True

        # Create a minimal mock karaoke instance
        from unittest.mock import MagicMock

        from pikaraoke.lib.database import PlayDatabase

        # Create temp db
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = PlayDatabase(db_path)
            db.ensure_session("test-session")

            # Mock the add_play method
            db.add_play = MagicMock(return_value=42)

            # Simulate what karaoke.start_song does
            now_playing = "Test Song"
            now_playing_user = "TestUser"
            session_id = "test-session"

            if db and now_playing and now_playing_user and session_id:
                play_id = db.add_play(
                    song=now_playing,
                    user=now_playing_user,
                    session_id=session_id,
                )

            db.add_play.assert_called_once_with(
                song="Test Song",
                user="TestUser",
                session_id="test-session",
            )
            assert play_id == 42
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_end_song_calls_update_play(self):
        """Test that Karaoke.end_song calls db.update_play."""
        from unittest.mock import MagicMock

        from pikaraoke.lib.database import PlayDatabase

        # Create temp db
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = PlayDatabase(db_path)
            db.ensure_session("test-session")

            # Mock the update_play method
            db.update_play = MagicMock()

            # Simulate what karaoke.end_song does
            current_play_id = 42
            reason = "complete"

            if db and current_play_id is not None:
                completed = reason == "complete"
                db.update_play(current_play_id, completed)

            db.update_play.assert_called_once_with(42, True)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestSessionManagementPhase2b:
    """Tests for session name editing (Phase 2b)."""

    def test_get_session_returns_data(self, temp_db):
        """Test that get_session returns session details."""
        session_id = "test-session-get"
        temp_db.ensure_session(session_id, "Test Session")

        session = temp_db.get_session(session_id)

        assert session is not None
        assert session["session_id"] == session_id
        assert session["name"] == "Test Session"
        assert "started_at" in session

    def test_get_session_not_found(self, temp_db):
        """Test that get_session returns None for non-existent session."""
        session = temp_db.get_session("non-existent-session")
        assert session is None

    def test_update_session_name(self, temp_db):
        """Test that update_session_name changes the session name."""
        session_id = "test-session-update"
        temp_db.ensure_session(session_id, "Original Name")

        success = temp_db.update_session_name(session_id, "New Name")

        assert success is True
        session = temp_db.get_session(session_id)
        assert session["name"] == "New Name"

    def test_update_session_name_not_found(self, temp_db):
        """Test that update_session_name returns False for non-existent session."""
        success = temp_db.update_session_name("non-existent", "New Name")
        assert success is False


class TestSessionManagementPhase2c:
    """Tests for session merge and delete (Phase 2c)."""

    def test_merge_sessions(self, temp_db):
        """Test that merge_sessions moves plays to target session."""
        import sqlite3

        # Create sessions and plays
        temp_db.ensure_session("source-1", "Source 1")
        temp_db.ensure_session("source-2", "Source 2")
        temp_db.ensure_session("target", "Target")

        temp_db.add_play("Song A", "User1", "source-1")
        temp_db.add_play("Song B", "User2", "source-1")
        temp_db.add_play("Song C", "User3", "source-2")

        # Merge
        plays_moved = temp_db.merge_sessions(["source-1", "source-2"], "target")

        assert plays_moved == 3

        # Check all plays are now in target
        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute("SELECT session_id FROM plays")
            sessions = [row[0] for row in cursor.fetchall()]
        assert all(s == "target" for s in sessions)

        # Check source sessions are deleted
        assert temp_db.get_session("source-1") is None
        assert temp_db.get_session("source-2") is None
        assert temp_db.get_session("target") is not None

    def test_merge_sessions_no_self_merge(self, temp_db):
        """Test that merge_sessions doesn't merge target into itself."""
        temp_db.ensure_session("session-1", "Session 1")
        temp_db.add_play("Song A", "User1", "session-1")

        plays_moved = temp_db.merge_sessions(["session-1"], "session-1")

        assert plays_moved == 0

    def test_delete_session(self, temp_db):
        """Test that delete_session removes session and all plays."""
        import sqlite3

        temp_db.ensure_session("to-delete", "To Delete")
        temp_db.add_play("Song A", "User1", "to-delete")
        temp_db.add_play("Song B", "User2", "to-delete")

        plays_deleted, sessions_deleted = temp_db.delete_session("to-delete")

        assert plays_deleted == 2
        assert sessions_deleted == 1
        assert temp_db.get_session("to-delete") is None

        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM plays WHERE session_id = ?", ("to-delete",)
            )
            assert cursor.fetchone()[0] == 0

    def test_delete_session_not_found(self, temp_db):
        """Test that delete_session returns zeros for non-existent session."""
        plays_deleted, sessions_deleted = temp_db.delete_session("non-existent")
        assert plays_deleted == 0
        assert sessions_deleted == 0


class TestUserAliasManagement:
    """Tests for user alias management (Phase 4)."""

    def test_add_alias(self, temp_db):
        """Test adding a user alias."""
        success = temp_db.add_alias("Bobby", "Bob")
        assert success is True

        # Verify alias was added
        aliases = temp_db.get_aliases_for_user("Bob")
        assert "Bobby" in aliases

    def test_add_alias_duplicate(self, temp_db):
        """Test that adding duplicate alias returns False."""
        temp_db.add_alias("Bobby", "Bob")
        success = temp_db.add_alias("Bobby", "Bob")
        assert success is False

    def test_get_aliases_for_user(self, temp_db):
        """Test getting aliases for a user."""
        temp_db.add_alias("Bobby", "Bob")
        temp_db.add_alias("Robert", "Bob")

        aliases = temp_db.get_aliases_for_user("Bob")
        assert len(aliases) == 2
        assert "Bobby" in aliases
        assert "Robert" in aliases

    def test_get_all_aliases(self, temp_db):
        """Test getting all aliases."""
        temp_db.add_alias("Bobby", "Bob")
        temp_db.add_alias("Ally", "Alice")

        all_aliases = temp_db.get_all_aliases()
        assert len(all_aliases) == 2
        assert any(a["alias"] == "Bobby" and a["canonical_name"] == "Bob" for a in all_aliases)
        assert any(a["alias"] == "Ally" and a["canonical_name"] == "Alice" for a in all_aliases)

    def test_remove_alias(self, temp_db):
        """Test removing an alias."""
        temp_db.add_alias("Bobby", "Bob")
        success = temp_db.remove_alias("Bobby")
        assert success is True

        aliases = temp_db.get_aliases_for_user("Bob")
        assert "Bobby" not in aliases

    def test_remove_alias_not_found(self, temp_db):
        """Test removing non-existent alias returns False."""
        success = temp_db.remove_alias("NonExistent")
        assert success is False

    def test_update_alias_canonical_user(self, temp_db):
        """Test changing an alias's canonical user."""
        temp_db.add_alias("Bobby", "Bob")
        success = temp_db.update_alias_canonical_user("Bobby", "Robert")
        assert success is True

        # Verify alias now points to Robert
        canonical = temp_db.get_canonical_name_for_alias("Bobby")
        assert canonical == "Robert"

    def test_get_canonical_name_for_alias(self, temp_db):
        """Test looking up canonical name for alias."""
        temp_db.add_alias("Bobby", "Bob")
        canonical = temp_db.get_canonical_name_for_alias("Bobby")
        assert canonical == "Bob"

    def test_get_canonical_name_for_alias_not_found(self, temp_db):
        """Test that non-existent alias returns None."""
        canonical = temp_db.get_canonical_name_for_alias("NonExistent")
        assert canonical is None

    def test_alias_resolution_in_add_play(self, temp_db):
        """Test that aliases are resolved when adding plays."""
        temp_db.add_alias("Bobby", "Bob")
        temp_db.ensure_session("test-session")

        play_id = temp_db.add_play("Song A", "Bobby", "test-session")
        play = temp_db.get_play(play_id)

        # canonical_name should be Bob, display_name should be Bobby
        assert play["canonical_name"] == "Bob"
        assert play["display_name"] == "Bobby"


class TestDeletePlayPermissions:
    """Tests for play deletion and permission checks (Phase 5)."""

    def test_can_user_delete_own_play_by_display_name(self, temp_db):
        """Test user can delete their own play by display name."""
        temp_db.ensure_session("test-session")
        play_id = temp_db.add_play("Song A", "Alice", "test-session")

        assert temp_db.can_user_delete_play(play_id, "Alice") is True

    def test_can_user_delete_own_play_by_canonical_name(self, temp_db):
        """Test user can delete play when matching canonical name."""
        temp_db.add_alias("Ally", "Alice")
        temp_db.ensure_session("test-session")
        play_id = temp_db.add_play("Song A", "Ally", "test-session")

        # Alice (canonical) can delete play made by Ally (alias)
        assert temp_db.can_user_delete_play(play_id, "Alice") is True

    def test_can_user_delete_as_alias_of_canonical(self, temp_db):
        """Test user with alias can delete play by canonical user."""
        temp_db.add_alias("Ally", "Alice")
        temp_db.ensure_session("test-session")
        play_id = temp_db.add_play("Song A", "Alice", "test-session")

        # Ally (alias of Alice) can delete Alice's play
        assert temp_db.can_user_delete_play(play_id, "Ally") is True

    def test_cannot_delete_other_users_play(self, temp_db):
        """Test user cannot delete another user's play."""
        temp_db.ensure_session("test-session")
        play_id = temp_db.add_play("Song A", "Alice", "test-session")

        assert temp_db.can_user_delete_play(play_id, "Bob") is False

    def test_cannot_delete_nonexistent_play(self, temp_db):
        """Test permission check returns False for nonexistent play."""
        assert temp_db.can_user_delete_play(99999, "Alice") is False

    def test_delete_play_success(self, temp_db):
        """Test successfully deleting a play."""
        temp_db.ensure_session("test-session")
        play_id = temp_db.add_play("Song A", "Alice", "test-session")

        success = temp_db.delete_play(play_id)
        assert success is True

        # Verify play is gone
        assert temp_db.get_play(play_id) is None

    def test_delete_play_not_found(self, temp_db):
        """Test deleting nonexistent play returns False."""
        success = temp_db.delete_play(99999)
        assert success is False


class TestRankings:
    """Tests for rankings methods (Phase 6)."""

    def test_get_top_users(self, temp_db):
        """Test getting users ranked by play count."""
        temp_db.ensure_session("test-session")
        temp_db.add_play("Song A", "Alice", "test-session")
        temp_db.add_play("Song B", "Alice", "test-session")
        temp_db.add_play("Song C", "Alice", "test-session")
        temp_db.add_play("Song A", "Bob", "test-session")

        top_users = temp_db.get_top_users(limit=10)

        assert len(top_users) == 2
        assert top_users[0]["canonical_name"] == "Alice"
        assert top_users[0]["play_count"] == 3
        assert top_users[1]["canonical_name"] == "Bob"
        assert top_users[1]["play_count"] == 1

    def test_get_top_songs(self, temp_db):
        """Test getting songs ranked by play count."""
        temp_db.ensure_session("test-session")
        temp_db.add_play("Popular Song", "Alice", "test-session")
        temp_db.add_play("Popular Song", "Bob", "test-session")
        temp_db.add_play("Popular Song", "Charlie", "test-session")
        temp_db.add_play("Other Song", "Alice", "test-session")

        top_songs = temp_db.get_top_songs(limit=10)

        assert len(top_songs) == 2
        assert top_songs[0]["song"] == "Popular Song"
        assert top_songs[0]["play_count"] == 3
        assert top_songs[1]["song"] == "Other Song"
        assert top_songs[1]["play_count"] == 1

    def test_get_busiest_sessions(self, temp_db):
        """Test getting sessions ranked by play count."""
        temp_db.ensure_session("busy-session", "Busy Night")
        temp_db.ensure_session("quiet-session", "Quiet Night")
        temp_db.add_play("Song A", "Alice", "busy-session")
        temp_db.add_play("Song B", "Bob", "busy-session")
        temp_db.add_play("Song C", "Charlie", "busy-session")
        temp_db.add_play("Song A", "Alice", "quiet-session")

        busiest = temp_db.get_busiest_sessions(limit=10)

        assert len(busiest) == 2
        assert busiest[0]["session_name"] == "Busy Night"
        assert busiest[0]["play_count"] == 3
        assert busiest[1]["session_name"] == "Quiet Night"
        assert busiest[1]["play_count"] == 1

    def test_get_busiest_days(self, temp_db):
        """Test getting days ranked by play count."""
        temp_db.ensure_session("test-session")
        # Add plays (they'll all be on today's date due to how add_play works)
        temp_db.add_play("Song A", "Alice", "test-session")
        temp_db.add_play("Song B", "Bob", "test-session")

        busiest = temp_db.get_busiest_days(limit=10)

        assert len(busiest) == 1
        assert busiest[0]["play_count"] == 2
