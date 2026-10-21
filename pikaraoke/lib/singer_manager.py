"""Singers: one row per performer, the id everything saved hangs off."""

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase


class SingerManager:
    """Owns the `singers` table: one row per performer, keyed by name.

    `singers.name` is the single place a name is stored, and plays point at
    `singers.id`, so every performer query reads the name through the row rather
    than carrying its own copy. The name is NOCASE-unique, so "Mike" and "mike"
    are one singer. A row is created the first time a name sings; stage D adds
    the star and the saved key, which create one the same way.
    """

    def __init__(self, db: KaraokeDatabase, events: EventSystem) -> None:
        self.db = db
        self.events = events

    def get_or_create(self, name: str) -> int:
        """Return the singer id for a name, creating the row on first use.

        A later play under a different casing of the same name refreshes the
        stored casing, so a name renders the way it was most recently typed --
        the behaviour the old _latest_casing() subquery gave, now held in the
        row rather than re-derived on every read.
        """
        rows = self.db.query("SELECT id, name FROM singers WHERE name = ? COLLATE NOCASE", (name,))
        if rows:
            singer_id, stored = rows[0]["id"], rows[0]["name"]
            if stored != name:
                self.db.execute("UPDATE singers SET name = ? WHERE id = ?", (name, singer_id))
            return singer_id
        return self.db.execute("INSERT INTO singers (name) VALUES (?)", (name,)).lastrowid

    def find(self, name: str) -> dict | None:
        """Return a singer row by name, or None when the name has no row yet.

        secret_hash is deliberately not selected: it must never ride along in an
        ordinary identity read.
        """
        rows = self.db.query(
            "SELECT id, name, created_at FROM singers WHERE name = ? COLLATE NOCASE", (name,)
        )
        return dict(rows[0]) if rows else None

    def backfill(self) -> None:
        """Give every play a singer_id, one row per distinct performer.

        The one-time upgrade of a history recorded before singers existed.
        Idempotent: on a database whose plays already point at a singer it
        inserts and updates nothing, so it is safe to run on every start-up.
        idx_plays_singer keeps the "still unlinked?" check off a table scan.

        Each new row takes the most recent casing of its name -- the row with
        the greatest id in the NOCASE group, id being monotonic with play order
        -- so a backfilled name reads the way _latest_casing() rendered it.
        """
        self.db.execute("""
            INSERT INTO singers (name)
            SELECT p.performer
            FROM plays p
            JOIN (
                SELECT MAX(id) AS latest_id
                FROM plays
                GROUP BY performer COLLATE NOCASE
            ) latest ON p.id = latest.latest_id
            WHERE NOT EXISTS (
                SELECT 1 FROM singers s WHERE s.name = p.performer COLLATE NOCASE
            )
            """)
        self.db.execute("""
            UPDATE plays SET singer_id = (
                SELECT id FROM singers WHERE name = plays.performer COLLATE NOCASE
            )
            WHERE singer_id IS NULL
            """)
