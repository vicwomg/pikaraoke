"""Singers: one row per performer, the id everything saved hangs off."""

import unicodedata
from collections.abc import Callable

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase

# The name is the singer's identity -- plays, favourites and the coming
# name-lock all key on it -- so it is canonicalised to one shape before storage
# or comparison, and a look-alike cannot shadow it. No minimum beyond one
# visible character, so a single ideograph is a valid name.
SINGER_NAME_MAX_LENGTH = 30


def normalize_name(raw: str) -> str:
    """Canonical form of a singer name: the one shape stored and compared.

    NFC (not NFKC, which would rewrite full-width and compatibility CJK), drop
    control and format characters, collapse internal whitespace, trim. Idempotent.
    An empty result is a blank name; callers also reject over SINGER_NAME_MAX_LENGTH.
    """
    text = unicodedata.normalize("NFC", raw)
    # Drop non-whitespace control/format chars (a tab is Cc, but split() below
    # must still see it as a separator, so whitespace is kept through this).
    text = "".join(c for c in text if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(text.split())


class SingerManager:
    """Owns the `singers` table: one row per performer, keyed by name.

    `singers.name` is the single place a name is stored, and plays point at
    `singers.id`, so every performer query reads the name through the row rather
    than carrying its own copy. The name is NOCASE-unique, so "Mike" and "mike"
    are one singer. A row is created the first time a name sings or stars a song.

    It also owns `singer_songs`, where a singer's favourites live (the saved key
    shares the row but has no UI yet), keyed on the song's durable identity so a
    star outlives a rename and a re-download.
    """

    def __init__(
        self,
        db: KaraokeDatabase,
        events: EventSystem,
        song_display_name: Callable[[str], str],
    ) -> None:
        self.db = db
        self.events = events
        # A song's displayed title from its path -- SongManager.display_name_from_path.
        # A favourite stores it as the label of last resort, matching how plays do,
        # so a favourited local rip and its plays agree on one title.
        self.song_display_name = song_display_name
        self.events.on("song_renamed", self._on_song_renamed)

    def _on_song_renamed(self, song_id: int, title: str) -> None:
        """Keep a renamed song's favourites readable under its new title.

        Scoped to song_id, the live link, so only favourites of a song still in
        the library follow the rename; for a local rip, whose identity is the
        title, this is also what keeps the star attached to it.

        OR IGNORE: a rename can collide two local-rip favourites on the title the
        identity index keys on, and the row still resolves by its song link.
        """
        self.db.execute(
            "UPDATE OR IGNORE singer_songs SET song_title = ? WHERE song_id = ?", (title, song_id)
        )

    def get_or_create(self, name: str, *, refresh_casing: bool = True) -> int:
        """Return the singer id for a name, creating the row on first use.

        A later play under a different casing of the same name refreshes the
        stored casing, so a name renders the way it was most recently sung --
        the behaviour the old _latest_casing() subquery gave, now held in the
        row rather than re-derived on every read.

        refresh_casing is off for a star, which is not a performance and whose
        name may come from another device's cookie.

        Normalised here too, so a caller bypassing the route cannot store an
        uncanonicalised name; NFC keeps case, so casing still refreshes.
        """
        name = normalize_name(name)
        rows = self.db.query("SELECT id, name FROM singers WHERE name = ? COLLATE NOCASE", (name,))
        if rows:
            singer_id, stored = rows[0]["id"], rows[0]["name"]
            if refresh_casing and stored != name:
                self.db.execute("UPDATE singers SET name = ? WHERE id = ?", (name, singer_id))
            return singer_id
        return self.db.execute("INSERT INTO singers (name) VALUES (?)", (name,)).lastrowid

    def find(self, name: str) -> dict | None:
        """Return a singer row by name, or None when the name has no row yet.

        secret_hash is deliberately not selected: it must never ride along in an
        ordinary identity read.
        """
        name = normalize_name(name)
        rows = self.db.query(
            "SELECT id, name, created_at FROM singers WHERE name = ? COLLATE NOCASE", (name,)
        )
        return dict(rows[0]) if rows else None

    def rename(self, old_name: str, new_name: str) -> bool:
        """Move a singer's row from old_name to new_name; True when a row moved.

        Everything a singer owns -- plays, favourites, preferences -- hangs off
        singers.id, so renaming the one row carries all of it with no further
        writes. A name that has never sung or starred has no row, and the
        caller's queue and now-playing moves are then the whole rename.

        The caller guarantees new_name is free (Karaoke.rename_singer refuses a
        taken target), so this never collides; a change of casing alone updates
        the same row, since new_name resolves back to it under NOCASE.
        """
        old_name = normalize_name(old_name)
        new_name = normalize_name(new_name)
        rows = self.db.query("SELECT id FROM singers WHERE name = ? COLLATE NOCASE", (old_name,))
        if not rows:
            return False
        self.db.execute("UPDATE singers SET name = ? WHERE id = ?", (new_name, rows[0]["id"]))
        return True

    def backfill(self) -> None:
        """Give every pre-singers play a singer_id; the one-time history upgrade.

        Reads only unlinked plays (idx_plays_singer), so it is a no-op once they
        are all linked and safe to run on every start-up. Each name goes through
        get_or_create, so a legacy name is normalised like every live path -- a
        raw copy would split from later plays -- taking the latest casing, oldest
        play first.
        """
        rows = self.db.query(
            "SELECT performer, MAX(id) AS latest_id FROM plays "
            "WHERE singer_id IS NULL GROUP BY performer ORDER BY latest_id"
        )
        for row in rows:
            singer_id = self.get_or_create(row["performer"])
            self.db.execute(
                "UPDATE plays SET singer_id = ? WHERE performer = ? AND singer_id IS NULL",
                (singer_id, row["performer"]),
            )

    # ------------------------------------------------------------------
    # Favourites
    # ------------------------------------------------------------------

    def _find_song_row(
        self, singer_id: int, song_id: int | None, youtube_id: str | None, title: str
    ) -> dict | None:
        """Find a singer's row for one song, by its most stable key.

        A YouTube song matches on its id; a local rip on its live song link or
        stored title (link first), so a rename or title-tidy toggle cannot hide
        it while a repeat star still upserts on the title the index keys on.
        """
        if youtube_id:
            rows = self.db.query(
                "SELECT id, semitones FROM singer_songs WHERE singer_id = ? AND youtube_id = ?",
                (singer_id, youtube_id),
            )
        else:
            rows = self.db.query(
                "SELECT id, semitones FROM singer_songs "
                "WHERE singer_id = ? AND youtube_id IS NULL "
                "AND (song_id = ? OR song_title = ? COLLATE NOCASE) "
                "ORDER BY (song_id = ?) DESC LIMIT 1",
                (singer_id, song_id, title, song_id),
            )
        return dict(rows[0]) if rows else None

    def add_favourite(self, name: str, song_path: str) -> None:
        """Star a song for a singer, creating the singer row on first use.

        Idempotent: starring an already-starred song refreshes the live link and
        title and leaves one row. The song_id and youtube_id are re-read each
        time so a star placed before a re-download picks up the new library row.

        A path not in the library is ignored, so an open route cannot grow the
        table with rows no view can surface.
        """
        song_id, youtube_id = self.db.get_song_identity(song_path)
        if song_id is None:
            return
        title = self.song_display_name(song_path)
        singer_id = self.get_or_create(name, refresh_casing=False)
        existing = self._find_song_row(singer_id, song_id, youtube_id, title)
        if existing:
            self.db.execute(
                "UPDATE singer_songs SET starred = 1, song_id = ?, song_title = ? WHERE id = ?",
                (song_id, title, existing["id"]),
            )
        else:
            self.db.execute(
                "INSERT INTO singer_songs (singer_id, song_id, youtube_id, song_title, starred) "
                "VALUES (?, ?, ?, ?, 1)",
                (singer_id, song_id, youtube_id, title),
            )

    def remove_favourite(self, name: str, song_path: str) -> None:
        """Clear a singer's star on a song.

        The row is deleted when nothing else is saved on it, which in this scope
        is always -- the saved key has no UI yet. A name or song that was never
        starred is a no-op.
        """
        singer = self.find(name)
        if singer is None:
            return
        song_id, youtube_id = self.db.get_song_identity(song_path)
        title = self.song_display_name(song_path)
        existing = self._find_song_row(singer["id"], song_id, youtube_id, title)
        if existing is None:
            return
        if existing["semitones"] is None:
            self.db.execute("DELETE FROM singer_songs WHERE id = ?", (existing["id"],))
        else:
            self.db.execute("UPDATE singer_songs SET starred = 0 WHERE id = ?", (existing["id"],))

    def get_favourite_paths(self, name: str) -> set[str]:
        """Return the current library paths of a singer's starred songs.

        Resolved by youtube_id first, so a re-downloaded song comes back by
        itself, then by the live song link for a local rip. A starred song no
        longer in the library resolves to nothing and drops out, so a caller can
        read membership as "in the library and starred by this singer".
        """
        singer = self.find(name)
        if singer is None:
            return set()
        rows = self.db.query(
            """
            SELECT s.file_path
            FROM singer_songs ss
            JOIN songs s ON (ss.youtube_id IS NOT NULL AND s.youtube_id = ss.youtube_id)
                         OR (ss.youtube_id IS NULL AND s.id = ss.song_id)
            WHERE ss.singer_id = ? AND ss.starred = 1
            """,
            (singer["id"],),
        )
        return {row["file_path"] for row in rows}
