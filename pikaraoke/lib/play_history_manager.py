"""Play history: session tracking, play recording, and reporting queries."""

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


# Sortable columns for the play log, keyed by what the UI sends. These
# interpolate into SQL, so the sort key is looked up here and never used raw.
# Songs sort by path rather than display title: the title is derived in Python,
# and path order still groups every play of the same song together.
_PLAY_SORTS = {
    "played_at": "p.played_at",
    "performer": "p.performer COLLATE NOCASE",
    "song": "s.file_path COLLATE NOCASE",
}


def _local(column: str, alias: str) -> str:
    """Render a UTC timestamp column as local time.

    Timestamps are stored UTC so that ordering survives a daylight-saving
    rollback, but the host only ever wants wall-clock time. Converting here in SQL
    keeps it to one place, rather than one conversion in Python for the
    server-rendered pages and another in JavaScript for the fetched ones.
    """
    return f"datetime({column}, 'localtime') AS {alias}"


class PlayHistoryManager:
    """Records who sang what and when, grouped into sessions.

    Sessions are time brackets the host names from the Sessions page, but one
    auto-starts on the first play so a forgotten Start button never loses a
    night's data.
    """

    def __init__(self, db: KaraokeDatabase, events: EventSystem) -> None:
        self.db = db
        self.events = events
        # The play still in flight, or None when nothing is playing. A play row
        # is written when the song starts, so `completed` is only meaningful
        # once it ends; the UI needs this to tell "still playing" from
        # "was skipped". The song and performer alongside it identify that
        # performance, so a transpose can recognise its own restart.
        self.current_play_id: int | None = None
        self._current_song_id: int | None = None
        self._current_performer: str | None = None
        self._resuming = False
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
            f"""
            SELECT id, uuid, name, {_local("started_at", "started_at")}, ended_at
            FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1
            """
        )
        return dict(rows[0]) if rows else None

    def get_current_session_name(self) -> str | None:
        """Return the open session's name, or None when it is unnamed or absent.

        Sessions auto-start unnamed on the first play, so callers that surface
        the name to a user (the nav ribbon, the splash screen) want those
        treated the same as no session at all rather than rendering "None".
        """
        session = self.get_current_session()
        return session["name"] if session else None

    def get_sessions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return a page of sessions, newest first, each with its play count.

        The count is a correlated subquery rather than a join-and-group so the
        LIMIT bounds the work: grouping first would aggregate every play ever
        recorded just to render ten rows.
        """
        rows = self.db.query(
            f"""
            SELECT s.id, s.uuid, s.name,
                   {_local("s.started_at", "started_at")},
                   {_local("s.ended_at", "ended_at")},
                   (SELECT COUNT(*) FROM plays p WHERE p.session_id = s.id) AS play_count
            FROM sessions s
            ORDER BY s.started_at DESC, s.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in rows]

    def activate_session(self, session_uuid: str) -> bool:
        """Reopen a session so new plays land in it, closing any other open one.

        Ending a session is otherwise one-way, so a mis-clicked End would split
        a night across two sessions with no way to rejoin them.
        """
        if not self.db.query("SELECT id FROM sessions WHERE uuid = ?", (session_uuid,)):
            return False
        self.end_session()
        self.db.execute("UPDATE sessions SET ended_at = NULL WHERE uuid = ?", (session_uuid,))
        return True

    def count_sessions(self) -> int:
        """Return the total number of sessions, so a paged list can say so."""
        return self.db.query("SELECT COUNT(*) FROM sessions")[0][0]

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
        if self._resuming:
            self._resuming = False
            if self._is_current_play(song_id, performer):
                # Same performance restarting in a new key. Reusing the open row
                # keeps it to one play, so a transpose cannot inflate rankings.
                return

        session = self.get_current_session()
        if session is None:
            self.start_session()
            session = self.get_current_session()

        cursor = self.db.execute(
            "INSERT INTO plays (session_id, song_id, performer) VALUES (?, ?, ?)",
            (session["id"], song_id, performer),
        )
        self.current_play_id = cursor.lastrowid
        self._current_song_id = song_id
        self._current_performer = performer

    def _is_current_play(self, song_id: int | None, performer: str) -> bool:
        """Is the in-flight play this same song by this same performer?

        Guards the resume: if the restart never arrives (the file vanished, say)
        the flag would otherwise swallow whatever played next.
        """
        if self.current_play_id is None:
            return False
        return self._current_song_id == song_id and self._current_performer == performer

    def _on_song_ended(self, reason: str | None = None) -> None:
        """Resolve the pending play's completed flag from the end reason.

        end_song() fires on every ending path, so only reason == "complete"
        marks the song as sung through; "skip" and "timeout" do not.
        """
        # A transpose ends the stream but not the performance: hold the row open
        # so the restart in the new key reuses it instead of logging a second.
        if reason == "transpose" and self.current_play_id is not None:
            self._resuming = True
            return

        self._resuming = False
        if self.current_play_id is None:
            return
        if reason == "complete":
            self.db.execute("UPDATE plays SET completed = 1 WHERE id = ?", (self.current_play_id,))
        self.current_play_id = None
        self._current_song_id = None
        self._current_performer = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_plays(
        self,
        session_uuid: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "played_at",
        direction: str = "desc",
    ) -> list[dict]:
        """Return a page of the play log, optionally scoped to one session.

        Sorting happens here rather than in the page because the log is paged:
        sorting a single page would only order the rows already fetched.

        Args:
            sort: One of _PLAY_SORTS. Anything else falls back to played_at.
            direction: "asc", or descending for anything else.
        """
        order = _PLAY_SORTS.get(sort, _PLAY_SORTS["played_at"])
        ascending = "ASC" if direction == "asc" else "DESC"

        where = ""
        params: tuple = ()
        if session_uuid:
            where = "WHERE p.session_id = (SELECT id FROM sessions WHERE uuid = ?)"
            params = (session_uuid,)

        rows = self.db.query(
            f"""
            SELECT p.id, {_local("p.played_at", "played_at")}, p.performer, p.completed,
                   s.file_path
            FROM plays p
            LEFT JOIN songs s ON s.id = p.song_id
            {where}
            ORDER BY {order} {ascending}, p.id {ascending}
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

    def get_singers(self, session_uuid: str | None = None, limit: int | None = None) -> list[dict]:
        """Return performers with play counts, most active first.

        Scoped to one session when given; otherwise every performer on record.
        The casing subquery stays unscoped so a name renders the same way
        wherever it appears.

        Args:
            limit: Cap on rows returned. Callers showing a leaderboard or an
                autocomplete want a handful, and the full list grows without
                bound over a venue's lifetime.
        """
        where = ""
        params: tuple = ()
        if session_uuid:
            where = "WHERE p.session_id = (SELECT id FROM sessions WHERE uuid = ?)"
            params = (session_uuid,)

        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params += (limit,)

        rows = self.db.query(
            f"""
            SELECT ({_LATEST_CASING}) AS performer,
                   COUNT(*) AS play_count,
                   {_local("MAX(p.played_at)", "last_played")}
            FROM plays p
            {where}
            GROUP BY p.performer COLLATE NOCASE
            ORDER BY play_count DESC, last_played DESC
            {limit_clause}
            """,
            params,
        )
        return [dict(row) for row in rows]

    def get_top_songs(self, limit: int = 20) -> list[dict]:
        """Return the most-played songs. Songs deleted from the library are omitted."""
        rows = self.db.query(
            """
            SELECT s.file_path, COUNT(*) AS play_count
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
            f"""
            SELECT {_local("p.played_at", "played_at")}, p.performer, p.completed,
                   s.file_path
            FROM plays p
            LEFT JOIN songs s ON s.id = p.song_id
            WHERE p.session_id = (SELECT id FROM sessions WHERE uuid = ?)
            ORDER BY p.played_at, p.id
            """,
            (session_uuid,),
        )
        return [dict(row) for row in rows]
