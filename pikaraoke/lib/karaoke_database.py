"""SQLite database layer for persistent song library storage."""

import os
import sqlite3
import threading

from pikaraoke.lib.get_platform import get_data_directory

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    youtube_id TEXT,
    format TEXT NOT NULL,
    artist TEXT,
    title TEXT,
    variant TEXT,
    year INTEGER,
    genre TEXT,
    metadata_status TEXT DEFAULT 'pending',
    enrichment_attempts INTEGER DEFAULT 0,
    last_enrichment_attempt TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_youtube_id ON songs(youtube_id);
CREATE INDEX IF NOT EXISTS idx_artist ON songs(artist);
CREATE INDEX IF NOT EXISTS idx_title ON songs(title);
CREATE INDEX IF NOT EXISTS idx_metadata_status ON songs(metadata_status);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class KaraokeDatabase:
    """Persistent song library backed by SQLite.

    Pure data layer with no filesystem operations. All paths are stored as
    native OS strings (str(path), never as_posix()).
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = os.path.join(get_data_directory(), "pikaraoke.db")
        self._db_path = db_path
        # All operations (including reads) share a single connection, so the
        # lock is required for thread safety -- Python's sqlite3.Connection is
        # not thread-safe even with check_same_thread=False. WAL mode benefits
        # crash recovery and write performance; Python-level read concurrency
        # would require separate connections per reader.
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        with self._conn:
            self._conn.execute("PRAGMA user_version = 1")

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_all_song_paths(self) -> list[str]:
        """Return all song file paths (unsorted; SongList handles sort order)."""
        with self._lock:
            rows = self._conn.execute("SELECT file_path FROM songs").fetchall()
            return [row[0] for row in rows]

    def get_song_count(self) -> int:
        """Return the total number of songs in the library."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]

    def get_format(self, file_path: str) -> str | None:
        """Return the format string for a song, or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT format FROM songs WHERE file_path = ?", (file_path,)
            ).fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Batch write operations (used by LibraryScanner)
    # ------------------------------------------------------------------

    def insert_songs(self, songs: list[dict]) -> None:
        """Batch-insert song records. Silently ignores duplicate file_paths."""
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO songs (file_path, youtube_id, format)
                VALUES (:file_path, :youtube_id, :format)
                """,
                songs,
            )

    def update_paths(self, moves: list[tuple[str, str]]) -> None:
        """Batch-update file paths for moved songs.

        Args:
            moves: List of (old_path, new_path) tuples.
        """
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE songs SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?",
                [(new, old) for old, new in moves],
            )

    def delete_by_paths(self, file_paths: list[str]) -> None:
        """Batch-delete songs by file path."""
        with self._lock, self._conn:
            self._conn.executemany(
                "DELETE FROM songs WHERE file_path = ?",
                [(p,) for p in file_paths],
            )

    def apply_scan_diff(
        self,
        moves: list[tuple[str, str]],
        inserts: list[dict],
        deletes: list[str],
    ) -> None:
        """Apply a complete scan diff atomically in a single transaction."""
        with self._lock, self._conn:
            if moves:
                self._conn.executemany(
                    "UPDATE songs SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?",
                    [(new, old) for old, new in moves],
                )
            if inserts:
                self._conn.executemany(
                    """
                    INSERT OR IGNORE INTO songs (file_path, youtube_id, format)
                    VALUES (:file_path, :youtube_id, :format)
                    """,
                    inserts,
                )
            if deletes:
                self._conn.executemany(
                    "DELETE FROM songs WHERE file_path = ?",
                    [(p,) for p in deletes],
                )

    # ------------------------------------------------------------------
    # Single-record write operations (delegate to batch methods)
    # ------------------------------------------------------------------

    def delete_by_path(self, file_path: str) -> None:
        """Delete a single song by file path (UI-triggered delete)."""
        self.delete_by_paths([file_path])

    def update_path(self, old_path: str, new_path: str) -> None:
        """Update a single song's file path (UI-triggered rename)."""
        self.update_paths([(old_path, new_path)])

    # ------------------------------------------------------------------
    # Metadata (app-level key-value store)
    # ------------------------------------------------------------------

    def get_metadata(self, key: str) -> str | None:
        """Return the value for a metadata key, or None if not set."""
        with self._lock:
            row = self._conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        """Set a metadata key-value pair (upsert)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def check_integrity(self) -> tuple[bool, str]:
        """Run PRAGMA integrity_check. Returns (ok, message)."""
        with self._lock:
            result = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
            return result == "ok", result

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
