"""Play history: session tracking, play recording, and KJ reporting queries."""

import uuid

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase

# Selects the most recently used casing of each performer name, so that
# "Mike" and "mike" rank as one person rather than forking the leaderboard.
_LATEST_CASING = """
    SELECT p2.performer FROM plays p2
    WHERE p2.performer = p.performer COLLATE NOCASE
    ORDER BY p2.played_at DESC, p2.id DESC LIMIT 1
"""


class PlayHistoryManager:
    """Records who sang what and when, grouped into sessions.

    Sessions are time brackets the KJ names from the History page, but one
    auto-starts on the first play so a forgotten Start button never loses a
    night's data.
    """

    def __init__(self, db: KaraokeDatabase, events: EventSystem) -> None:
        self.db = db
        self.events = events
        self._current_play_id: int | None = None
        self.events.on("song_ended", self._on_song_ended)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def start_session(self, name: str | None = None) -> str:
        """Start a session and return its UUID.

        Closes any session still open, so "current" is never ambiguous.
        """
        self.end_session()
        session_uuid = str(uuid.uuid4())
        self.db.execute("INSERT INTO sessions (uuid, name) VALUES (?, ?)", (session_uuid, name))
        return session_uuid

    def end_session(self) -> None:
        """Close the active session, if there is one."""
        self.db.execute("UPDATE sessions SET ended_at = CURRENT_TIMESTAMP WHERE ended_at IS NULL")

    def get_current_session(self) -> dict | None:
        """Return the open session, or None if none is active."""
        rows = self.db.query(
            "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        )
        return dict(rows[0]) if rows else None

    def get_sessions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return sessions newest first, each with its play count."""
        rows = self.db.query(
            """
            SELECT s.uuid, s.name, s.started_at, s.ended_at, COUNT(p.id) AS play_count
            FROM sessions s
            LEFT JOIN plays p ON p.session_id = s.id
            GROUP BY s.id
            ORDER BY s.started_at DESC, s.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in rows]

    def rename_session(self, session_uuid: str, name: str) -> bool:
        """Name a session (typically one that auto-started unnamed)."""
        cursor = self.db.execute(
            "UPDATE sessions SET name = ? WHERE uuid = ?", (name, session_uuid)
        )
        return cursor.rowcount > 0

    def delete_session(self, session_uuid: str) -> bool:
        """Delete a session and, by cascade, all of its plays."""
        cursor = self.db.execute("DELETE FROM sessions WHERE uuid = ?", (session_uuid,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Play recording
    # ------------------------------------------------------------------

    def record_play(self, song_id: int | None, performer: str) -> None:
        """Record a play against the active session, auto-starting one if needed."""
        session = self.get_current_session()
        if session is None:
            self.start_session()
            session = self.get_current_session()

        cursor = self.db.execute(
            "INSERT INTO plays (session_id, song_id, performer) VALUES (?, ?, ?)",
            (session["id"], song_id, performer),
        )
        self._current_play_id = cursor.lastrowid

    def _on_song_ended(self, reason: str | None = None) -> None:
        """Resolve the pending play's completed flag from the end reason.

        end_song() fires on every ending path, so only reason == "complete"
        marks the song as sung through; "skip" and "timeout" do not.
        """
        if self._current_play_id is None:
            return
        if reason == "complete":
            self.db.execute("UPDATE plays SET completed = 1 WHERE id = ?", (self._current_play_id,))
        self._current_play_id = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_plays(
        self, session_uuid: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """Return the play log newest first, optionally scoped to one session."""
        where = ""
        params: tuple = ()
        if session_uuid:
            where = "WHERE p.session_id = (SELECT id FROM sessions WHERE uuid = ?)"
            params = (session_uuid,)

        rows = self.db.query(
            f"""
            SELECT p.id, p.played_at, p.performer, p.completed,
                   p.song_id, s.file_path, s.artist, s.title
            FROM plays p
            LEFT JOIN songs s ON s.id = p.song_id
            {where}
            ORDER BY p.played_at DESC, p.id DESC
            LIMIT ? OFFSET ?
            """,
            params + (limit, offset),
        )
        return [dict(row) for row in rows]

    def count_plays(self, session_uuid: str | None = None) -> int:
        """Return the total number of plays, optionally scoped to one session."""
        if session_uuid:
            rows = self.db.query(
                "SELECT COUNT(*) FROM plays WHERE session_id = "
                "(SELECT id FROM sessions WHERE uuid = ?)",
                (session_uuid,),
            )
        else:
            rows = self.db.query("SELECT COUNT(*) FROM plays")
        return rows[0][0]

    def get_singers(self) -> list[dict]:
        """Return performers with play counts, most active first."""
        rows = self.db.query(
            f"""
            SELECT ({_LATEST_CASING}) AS performer,
                   COUNT(*) AS play_count,
                   MAX(p.played_at) AS last_played
            FROM plays p
            GROUP BY p.performer COLLATE NOCASE
            ORDER BY play_count DESC, last_played DESC
            """
        )
        return [dict(row) for row in rows]

    def get_top_songs(self, limit: int = 20) -> list[dict]:
        """Return the most-played songs. Songs deleted from the library are omitted."""
        rows = self.db.query(
            """
            SELECT p.song_id, s.file_path, s.artist, s.title, COUNT(*) AS play_count
            FROM plays p
            JOIN songs s ON s.id = p.song_id
            GROUP BY p.song_id
            ORDER BY play_count DESC, s.file_path
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in rows]

    def delete_play(self, play_id: int) -> bool:
        """Delete a single play from the log."""
        cursor = self.db.execute("DELETE FROM plays WHERE id = ?", (play_id,))
        return cursor.rowcount > 0

    def export_plays(self, session_uuid: str) -> list[dict]:
        """Return a session's plays oldest first, for CSV export."""
        rows = self.db.query(
            """
            SELECT p.played_at, p.performer, p.completed, s.file_path, s.artist, s.title
            FROM plays p
            LEFT JOIN songs s ON s.id = p.song_id
            WHERE p.session_id = (SELECT id FROM sessions WHERE uuid = ?)
            ORDER BY p.played_at, p.id
            """,
            (session_uuid,),
        )
        return [dict(row) for row in rows]
