"""SQLite database for play history tracking."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime


class PlayDatabase:
    """Manages play history in a SQLite database.

    Tracks songs played, users, sessions, and user aliases.
    Each server run creates a new session identified by UUID.
    """

    def __init__(self, db_path: str) -> None:
        """Initialize the database connection.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self.init_db()

    def init_db(self) -> None:
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            self._create_tables_if_needed(conn)
            conn.commit()

    def _create_tables_if_needed(self, conn: sqlite3.Connection) -> None:
        """Create database tables and indexes if they don't exist."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                canonical_name TEXT PRIMARY KEY
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_aliases (
                alias TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                FOREIGN KEY (canonical_name) REFERENCES users (canonical_name)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                started_at TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT,
                song TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                FOREIGN KEY (canonical_name) REFERENCES users (canonical_name),
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
            """
        )

        # Create indexes for better query performance
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plays_canonical_name ON plays(canonical_name)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plays_timestamp ON plays(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plays_session_id ON plays(session_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_aliases_canonical_name "
            "ON user_aliases(canonical_name)"
        )

    def check_integrity(self) -> tuple[bool, str]:
        """Check database integrity using PRAGMA integrity_check.

        Returns:
            Tuple of (is_ok, message) where is_ok is True if database is healthy.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("PRAGMA integrity_check")
                result = cursor.fetchone()
                if result and result[0] == "ok":
                    return True, "Database integrity check passed"
                return False, f"Database integrity check failed: {result}"
        except sqlite3.Error as e:
            return False, f"Database error: {e}"

    @staticmethod
    def check_and_recover_db(db_path: str, force_recreate: bool = False) -> bool:
        """Check database integrity at startup and recover if needed.

        Args:
            db_path: Path to the database file.
            force_recreate: If True, rename corrupt DB and create new one.
                           If False, exit on corruption.

        Returns:
            True if database is ready to use.

        Raises:
            SystemExit: If database is corrupt and force_recreate is False.
        """
        if not os.path.exists(db_path):
            logging.info(f"Database does not exist, will be created: {db_path}")
            return True

        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.execute("PRAGMA integrity_check")
                result = cursor.fetchone()
                if result and result[0] == "ok":
                    logging.info("Database integrity check passed")
                    return True
        except sqlite3.Error as e:
            logging.error(f"Database error during integrity check: {e}")

        # Database is corrupt
        if force_recreate:
            timestamp = datetime.now().strftime("%Y-%m-%d-%s")
            failed_path = f"{db_path}.failed-{timestamp}"
            logging.warning(f"Database corrupt, renaming to {failed_path} and creating new one")
            try:
                os.rename(db_path, failed_path)
                return True
            except OSError as e:
                logging.error(f"Failed to rename corrupt database: {e}")
                sys.exit(1)
        else:
            logging.error(
                "Database is corrupt. Use --force-recreate-db to rename and create a new database."
            )
            sys.exit(1)

    def ensure_session(self, session_id: str, default_name: str | None = None) -> None:
        """Ensure a session exists in the database.

        Args:
            session_id: UUID string for the session.
            default_name: Default session name (uses YYYY-MM-DD if not provided).
        """
        if default_name is None:
            default_name = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
            )
            if cursor.fetchone() is None:
                started_at = datetime.now().isoformat()
                conn.execute(
                    "INSERT INTO sessions (session_id, name, started_at) VALUES (?, ?, ?)",
                    (session_id, default_name, started_at),
                )
                conn.commit()
                logging.info(f"Created session: {default_name} ({session_id})")

    def _resolve_canonical_user(self, user: str) -> str:
        """Resolve a user name to its canonical name, creating user if needed.

        Args:
            user: User name or alias.

        Returns:
            The canonical user name.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT canonical_name FROM user_aliases WHERE alias = ?", (user,)
            )
            result = cursor.fetchone()
            if result:
                return result[0]
            # Not an alias, ensure user exists in users table
            conn.execute("INSERT OR IGNORE INTO users (canonical_name) VALUES (?)", (user,))
            return user

    def add_play(self, song: str, user: str, session_id: str) -> int:
        """Add a new play record at song start.

        Args:
            song: Song title or filename.
            user: User/singer name (will be resolved to canonical).
            session_id: Current session UUID.

        Returns:
            The play record ID for later update.
        """
        canonical_name = self._resolve_canonical_user(user)
        display_name = user  # Store original name before resolution

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO plays (timestamp, session_id, canonical_name, display_name, song)
                VALUES (datetime('now'), ?, ?, ?, ?)
                """,
                (session_id, canonical_name, display_name, song),
            )
            conn.commit()
            play_id = cursor.lastrowid
            logging.debug(f"Added play record {play_id}: {song} by {user}")
            return play_id  # type: ignore[return-value]

    def update_play(self, play_id: int, completed: bool) -> None:
        """Update a play record when song ends.

        Args:
            play_id: ID of the play record to update.
            completed: True if song finished, False if skipped.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE plays SET completed = ? WHERE id = ?",
                (1 if completed else 0, play_id),
            )
            conn.commit()
            logging.debug(f"Updated play record {play_id}: completed={completed}")

    def get_play(self, play_id: int) -> dict | None:
        """Get a single play record by ID.

        Args:
            play_id: ID of the play record.

        Returns:
            Dictionary with play data or None if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT p.*, s.name as session_name
                FROM plays p
                LEFT JOIN sessions s ON p.session_id = s.session_id
                WHERE p.id = ?
                """,
                (play_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def _build_filter_clause(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        session_ids: list[str] | None = None,
        user_filter: str | None = None,
        alias_filter: str | None = None,
        song_filter: str | None = None,
        table_prefix: str = "p",
    ) -> tuple[str, list]:
        """Build WHERE clause and parameters for filtering.

        Args:
            date_from: Start date (inclusive) in YYYY-MM-DD format.
            date_to: End date (inclusive) in YYYY-MM-DD format.
            session_ids: List of session IDs to filter by.
            user_filter: Filter by canonical user name.
            alias_filter: Filter by specific display_name (requires user_filter).
            song_filter: Filter by exact song name.
            table_prefix: Table alias prefix for column names.

        Returns:
            Tuple of (where_clause, params).
        """
        conditions = []
        params: list = []

        if date_from:
            conditions.append(f"date({table_prefix}.timestamp) >= ?")
            params.append(date_from)

        if date_to:
            conditions.append(f"date({table_prefix}.timestamp) <= ?")
            params.append(date_to)

        if session_ids:
            placeholders = ",".join("?" * len(session_ids))
            conditions.append(f"{table_prefix}.session_id IN ({placeholders})")
            params.extend(session_ids)

        if user_filter:
            canonical_user = self._resolve_canonical_user(user_filter)
            conditions.append(f"{table_prefix}.canonical_name = ?")
            params.append(canonical_user)
            # If alias_filter is set, also filter by display_name
            if alias_filter:
                conditions.append(f"{table_prefix}.display_name = ?")
                params.append(alias_filter)

        if song_filter:
            conditions.append(f"{table_prefix}.song = ?")
            params.append(song_filter)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        return where_clause, params

    def get_last_plays(
        self,
        limit: int = 20,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
        session_ids: list[str] | None = None,
        user_filter: str | None = None,
        alias_filter: str | None = None,
        song_filter: str | None = None,
        sort_by: str = "timestamp",
        sort_order: str = "DESC",
    ) -> list[dict]:
        """Get recent plays with filtering and pagination.

        Args:
            limit: Maximum number of records to return.
            offset: Number of records to skip.
            date_from: Start date filter (YYYY-MM-DD).
            date_to: End date filter (YYYY-MM-DD).
            session_ids: List of session IDs to filter by.
            user_filter: Filter by canonical user.
            alias_filter: Filter by specific display_name (requires user_filter).
            song_filter: Filter by exact song name.
            sort_by: Column to sort by (timestamp, song, canonical_name).
            sort_order: Sort direction (ASC or DESC).

        Returns:
            List of play records as dictionaries.
        """
        # Validate sort parameters
        allowed_sort_cols = {"timestamp", "song", "canonical_name", "session_id"}
        if sort_by not in allowed_sort_cols:
            sort_by = "timestamp"
        if sort_order.upper() not in ("ASC", "DESC"):
            sort_order = "DESC"

        where_clause, params = self._build_filter_clause(
            date_from, date_to, session_ids, user_filter, alias_filter, song_filter
        )

        query = f"""
            SELECT p.id, p.timestamp, p.session_id, s.name as session_name,
                   p.canonical_name, p.display_name, p.song, p.completed
            FROM plays p
            LEFT JOIN sessions s ON p.session_id = s.session_id
            {where_clause}
            ORDER BY p.{sort_by} {sort_order}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_plays_count(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        session_ids: list[str] | None = None,
        user_filter: str | None = None,
        alias_filter: str | None = None,
        song_filter: str | None = None,
    ) -> int:
        """Get total count of plays matching filters.

        Args:
            date_from: Start date filter (YYYY-MM-DD).
            date_to: End date filter (YYYY-MM-DD).
            session_ids: List of session IDs to filter by.
            user_filter: Filter by canonical user.
            alias_filter: Filter by specific display_name (requires user_filter).
            song_filter: Filter by exact song name.

        Returns:
            Total count of matching plays.
        """
        where_clause, params = self._build_filter_clause(
            date_from, date_to, session_ids, user_filter, alias_filter, song_filter
        )

        query = f"SELECT COUNT(*) FROM plays p{where_clause}"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()[0]

    def get_session_ids_last_n(self, n: int) -> list[str]:
        """Get the last N session IDs ordered by most recent play.

        Args:
            n: Number of sessions to return.

        Returns:
            List of session IDs.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT session_id
                FROM plays
                GROUP BY session_id
                ORDER BY MAX(timestamp) DESC
                LIMIT ?
                """,
                (n,),
            )
            return [row[0] for row in cursor.fetchall()]

    def get_sessions_with_names(
        self, date_from: str | None = None, date_to: str | None = None
    ) -> list[dict]:
        """Get sessions with their names for dropdown filters.

        Args:
            date_from: Optional start date to filter sessions.
            date_to: Optional end date to filter sessions.

        Returns:
            List of session dictionaries with session_id, name, started_at.
        """
        conditions = []
        params: list = []

        if date_from or date_to:
            # Only return sessions that have plays in the date range
            subquery_conditions = []
            if date_from:
                subquery_conditions.append("date(p.timestamp) >= ?")
                params.append(date_from)
            if date_to:
                subquery_conditions.append("date(p.timestamp) <= ?")
                params.append(date_to)
            subquery_where = " AND ".join(subquery_conditions)
            conditions.append(
                f"s.session_id IN (SELECT DISTINCT session_id FROM plays p WHERE {subquery_where})"
            )

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT s.session_id, s.name, s.started_at
            FROM sessions s
            {where_clause}
            ORDER BY s.started_at DESC
        """

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_distinct_users(self) -> list[str]:
        """Get all distinct users (canonical names with plays).

        Returns:
            List of canonical user names.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT canonical_name
                FROM plays
                ORDER BY canonical_name
                """
            )
            return [row[0] for row in cursor.fetchall()]

    def get_distinct_dates(self) -> list[str]:
        """Get all distinct dates when plays occurred.

        Returns:
            List of date strings in YYYY-MM-DD format.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT DISTINCT date(timestamp) as date FROM plays ORDER BY date DESC"
            )
            return [row[0] for row in cursor.fetchall()]

    # --- Session Management (Phase 2b, 2c) ---

    def get_session(self, session_id: str) -> dict | None:
        """Get a single session by ID.

        Args:
            session_id: The session UUID.

        Returns:
            Dictionary with session data or None if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT session_id, name, started_at FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def update_session_name(self, session_id: str, name: str) -> bool:
        """Update a session's display name.

        Args:
            session_id: The session UUID.
            name: New display name for the session.

        Returns:
            True if update succeeded, False if session not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE sessions SET name = ? WHERE session_id = ?",
                (name, session_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def merge_sessions(self, source_session_ids: list[str], target_session_id: str) -> int:
        """Merge multiple sessions into a target session.

        Moves all plays from source sessions to target, then deletes source sessions.

        Args:
            source_session_ids: List of session IDs to merge from.
            target_session_id: Session ID to merge into.

        Returns:
            Number of plays moved.
        """
        if not source_session_ids or target_session_id in source_session_ids:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            placeholders = ",".join("?" * len(source_session_ids))

            # Move plays to target session
            cursor = conn.execute(
                f"UPDATE plays SET session_id = ? WHERE session_id IN ({placeholders})",
                [target_session_id] + source_session_ids,
            )
            plays_moved = cursor.rowcount

            # Delete source sessions
            conn.execute(
                f"DELETE FROM sessions WHERE session_id IN ({placeholders})",
                source_session_ids,
            )

            conn.commit()
            logging.info(
                f"Merged {len(source_session_ids)} sessions into {target_session_id}, "
                f"moved {plays_moved} plays"
            )
            return plays_moved

    def delete_session(self, session_id: str) -> tuple[int, int]:
        """Delete a session and all its plays.

        Args:
            session_id: The session UUID to delete.

        Returns:
            Tuple of (plays_deleted, sessions_deleted).
        """
        with sqlite3.connect(self.db_path) as conn:
            # Delete plays first
            cursor = conn.execute(
                "DELETE FROM plays WHERE session_id = ?",
                (session_id,),
            )
            plays_deleted = cursor.rowcount

            # Delete session
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            sessions_deleted = cursor.rowcount

            conn.commit()
            logging.info(f"Deleted session {session_id}: {plays_deleted} plays removed")
            return plays_deleted, sessions_deleted

    # --- User Alias Management (Phase 4) ---

    def get_aliases_for_user(self, canonical_name: str) -> list[str]:
        """Get all aliases for a canonical user.

        Args:
            canonical_name: The canonical user name.

        Returns:
            List of alias names for the user.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT alias FROM user_aliases WHERE canonical_name = ? ORDER BY alias",
                (canonical_name,),
            )
            return [row[0] for row in cursor.fetchall()]

    def get_all_aliases(self) -> list[dict]:
        """Get all user aliases.

        Returns:
            List of dicts with alias and canonical_name fields.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT alias, canonical_name FROM user_aliases ORDER BY canonical_name, alias"
            )
            return [dict(row) for row in cursor.fetchall()]

    def add_alias(self, alias: str, canonical_name: str) -> bool:
        """Add an alias for a canonical user.

        Args:
            alias: The alias name.
            canonical_name: The canonical user name to map to.

        Returns:
            True if alias was added, False if it already exists.
        """
        with sqlite3.connect(self.db_path) as conn:
            # Ensure canonical user exists
            conn.execute("INSERT OR IGNORE INTO users (canonical_name) VALUES (?)", (canonical_name,))

            try:
                conn.execute(
                    "INSERT INTO user_aliases (alias, canonical_name) VALUES (?, ?)",
                    (alias, canonical_name),
                )
                conn.commit()
                logging.info(f"Added alias '{alias}' -> '{canonical_name}'")
                return True
            except sqlite3.IntegrityError:
                # Alias already exists
                return False

    def remove_alias(self, alias: str) -> bool:
        """Remove an alias.

        Args:
            alias: The alias to remove.

        Returns:
            True if alias was removed, False if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM user_aliases WHERE alias = ?", (alias,))
            conn.commit()
            return cursor.rowcount > 0

    def update_alias_canonical_user(self, alias: str, new_canonical_name: str) -> bool:
        """Change the canonical user an alias points to.

        Args:
            alias: The alias to update.
            new_canonical_name: New canonical user name.

        Returns:
            True if alias was updated, False if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            # Ensure new canonical user exists
            conn.execute(
                "INSERT OR IGNORE INTO users (canonical_name) VALUES (?)", (new_canonical_name,)
            )

            cursor = conn.execute(
                "UPDATE user_aliases SET canonical_name = ? WHERE alias = ?",
                (new_canonical_name, alias),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_canonical_name_for_alias(self, alias: str) -> str | None:
        """Get the canonical name for an alias.

        Args:
            alias: The alias to look up.

        Returns:
            The canonical name or None if alias not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT canonical_name FROM user_aliases WHERE alias = ?", (alias,)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    # --- Play Record Management (Phase 5) ---

    def can_user_delete_play(self, play_id: int, username: str) -> bool:
        """Check if a user can delete a specific play record.

        A user can delete a play if:
        - Their name matches the canonical_name of the play
        - Their name matches the display_name of the play
        - Their name is an alias that maps to the canonical_name

        Args:
            play_id: The play record ID.
            username: The requesting user's name.

        Returns:
            True if user can delete the play.
        """
        play = self.get_play(play_id)
        if not play:
            return False

        # Direct match on display_name or canonical_name
        if username == play["display_name"] or username == play["canonical_name"]:
            return True

        # Check if username is an alias for the canonical user
        canonical = self.get_canonical_name_for_alias(username)
        if canonical and canonical == play["canonical_name"]:
            return True

        # Check if the play's canonical user resolves from the username
        resolved = self._resolve_canonical_user(username)
        return resolved == play["canonical_name"]

    def delete_play(self, play_id: int) -> bool:
        """Delete a play record by ID.

        Args:
            play_id: The play record ID.

        Returns:
            True if play was deleted, False if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM plays WHERE id = ?", (play_id,))
            conn.commit()
            return cursor.rowcount > 0

    # --- Rankings (Phase 6) ---

    def get_top_users(
        self,
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
        session_ids: list[str] | None = None,
    ) -> list[dict]:
        """Get users ranked by total songs played.

        Args:
            limit: Maximum number of results.
            date_from: Optional start date filter.
            date_to: Optional end date filter.
            session_ids: Optional session filter.

        Returns:
            List of dicts with canonical_name and play_count.
        """
        where_clause, params = self._build_filter_clause(
            date_from, date_to, session_ids, None, None
        )

        query = f"""
            SELECT canonical_name, COUNT(*) as play_count
            FROM plays p
            {where_clause}
            GROUP BY canonical_name
            ORDER BY play_count DESC
            LIMIT ?
        """
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_top_songs(
        self,
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
        session_ids: list[str] | None = None,
    ) -> list[dict]:
        """Get songs ranked by times played.

        Args:
            limit: Maximum number of results.
            date_from: Optional start date filter.
            date_to: Optional end date filter.
            session_ids: Optional session filter.

        Returns:
            List of dicts with song and play_count.
        """
        where_clause, params = self._build_filter_clause(
            date_from, date_to, session_ids, None, None
        )

        query = f"""
            SELECT song, COUNT(*) as play_count
            FROM plays p
            {where_clause}
            GROUP BY song
            ORDER BY play_count DESC
            LIMIT ?
        """
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_busiest_sessions(
        self,
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Get sessions ranked by number of plays.

        Args:
            limit: Maximum number of results.
            date_from: Optional start date filter.
            date_to: Optional end date filter.

        Returns:
            List of dicts with session_id, session_name, and play_count.
        """
        conditions = []
        params = []

        if date_from:
            conditions.append("date(p.timestamp) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(p.timestamp) <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT p.session_id, s.name as session_name, COUNT(*) as play_count
            FROM plays p
            LEFT JOIN sessions s ON p.session_id = s.session_id
            {where_clause}
            GROUP BY p.session_id
            ORDER BY play_count DESC
            LIMIT ?
        """
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_busiest_days(
        self,
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Get days ranked by number of plays.

        Args:
            limit: Maximum number of results.
            date_from: Optional start date filter.
            date_to: Optional end date filter.

        Returns:
            List of dicts with date and play_count.
        """
        conditions = []
        params = []

        if date_from:
            conditions.append("date(timestamp) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(timestamp) <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT date(timestamp) as date, COUNT(*) as play_count
            FROM plays
            {where_clause}
            GROUP BY date(timestamp)
            ORDER BY play_count DESC
            LIMIT ?
        """
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # --- Performers (Phase 7) ---

    def get_performers(
        self,
        limit: int = 100,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
        sort_by: str = "play_count",
        sort_order: str = "DESC",
    ) -> list[dict]:
        """Get all performers with their play counts and aliases.

        Args:
            limit: Maximum number of results.
            offset: Offset for pagination.
            date_from: Optional start date filter.
            date_to: Optional end date filter.
            sort_by: Column to sort by (canonical_name, play_count).
            sort_order: ASC or DESC.

        Returns:
            List of dicts with canonical_name, play_count, and aliases.
        """
        conditions = []
        params = []

        if date_from:
            conditions.append("date(p.timestamp) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(p.timestamp) <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        # Validate sort_by
        valid_sorts = {"canonical_name": "canonical_name", "play_count": "play_count"}
        sort_col = valid_sorts.get(sort_by, "play_count")
        order = "DESC" if sort_order.upper() == "DESC" else "ASC"

        query = f"""
            SELECT canonical_name, COUNT(*) as play_count
            FROM plays p
            {where_clause}
            GROUP BY canonical_name
            ORDER BY {sort_col} {order}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            performers = [dict(row) for row in cursor.fetchall()]

            # Add aliases for each performer
            for performer in performers:
                performer["aliases"] = self.get_aliases_for_user(performer["canonical_name"])

            return performers

    def get_performers_count(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> int:
        """Get count of distinct performers.

        Args:
            date_from: Optional start date filter.
            date_to: Optional end date filter.

        Returns:
            Count of distinct performers.
        """
        conditions = []
        params = []

        if date_from:
            conditions.append("date(timestamp) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(timestamp) <= ?")
            params.append(date_to)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"SELECT COUNT(DISTINCT canonical_name) FROM plays{where_clause}"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()[0]

    # --- Admin Play Editing (Phase 8) ---

    def update_play_user(
        self, play_id: int, display_name: str, canonical_name: str | None = None
    ) -> bool:
        """Update a play's user information.

        Args:
            play_id: The play record ID.
            display_name: New display name for the play.
            canonical_name: New canonical name. If None, resolved from display_name.

        Returns:
            True if play was updated, False if not found.
        """
        if canonical_name is None:
            canonical_name = self._resolve_canonical_user(display_name)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE plays SET display_name = ?, canonical_name = ? WHERE id = ?",
                (display_name, canonical_name, play_id),
            )
            conn.commit()
            return cursor.rowcount > 0
