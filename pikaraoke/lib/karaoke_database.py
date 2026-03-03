"""SQLite database layer for persistent song library storage."""

import os
import re
import sqlite3

from pikaraoke.lib.get_platform import get_data_directory

# YouTube ID is exactly 11 chars: letters, digits, underscores, hyphens
_PIKARAOKE_ID_RE = re.compile(r"---([A-Za-z0-9_-]{11})(?:\.|$)")
_YTDLP_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\](?:\.[^.]+)?$")

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
"""


def build_song_record(file_path: str) -> dict:
    """Construct a song dict ready for KaraokeDatabase.insert_songs().

    Inspects the file's directory for companion files (.cdg, .ass) to
    determine the correct format.
    """
    basename = os.path.basename(file_path)
    dirpath = os.path.dirname(file_path)
    try:
        files_in_dir = set(os.listdir(dirpath))
    except OSError:
        files_in_dir = set()
    return {
        "file_path": file_path,
        "youtube_id": _extract_youtube_id(basename),
        "format": _detect_format(file_path, files_in_dir),
    }


def _extract_youtube_id(filename: str) -> str | None:
    """Extract YouTube ID from PiKaraoke (---ID) or yt-dlp ([ID]) format."""
    m = _PIKARAOKE_ID_RE.search(filename)
    if m:
        return m.group(1)
    m = _YTDLP_ID_RE.search(filename)
    if m:
        return m.group(1)
    return None


def _detect_format(file_path: str, files_in_dir: set[str]) -> str:
    """Detect the song format, checking for companion files (.cdg, .ass)."""
    base, ext = os.path.splitext(os.path.basename(file_path))
    ext = ext.lower()
    if ext == ".mp3" and (base + ".cdg") in files_in_dir:
        return "cdg"
    if ext == ".mp4" and (base + ".ass") in files_in_dir:
        return "ass"
    return ext.lstrip(".")


class KaraokeDatabase:
    """Persistent song library backed by SQLite.

    Pure data layer with no filesystem operations. All paths are stored as
    native OS strings (str(path), never as_posix()).
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = os.path.join(get_data_directory(), "pikaraoke.db")
        self._db_path = db_path
        self._conn = self._connect()
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA user_version = 1")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_all_song_paths(self) -> list[str]:
        """Return all song file paths (unsorted; SongList handles sort order)."""
        rows = self._conn.execute("SELECT file_path FROM songs").fetchall()
        return [row[0] for row in rows]

    def get_song_count(self) -> int:
        """Return the total number of songs in the library."""
        return self._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]

    # ------------------------------------------------------------------
    # Batch write operations (used by LibraryScanner)
    # ------------------------------------------------------------------

    def insert_songs(self, songs: list[dict]) -> None:
        """Batch-insert song records. Silently ignores duplicate file_paths."""
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO songs (file_path, youtube_id, format)
            VALUES (:file_path, :youtube_id, :format)
            """,
            songs,
        )
        self._conn.commit()

    def update_paths(self, moves: list[tuple[str, str]]) -> None:
        """Batch-update file paths for moved songs.

        Args:
            moves: List of (old_path, new_path) tuples.
        """
        self._conn.executemany(
            "UPDATE songs SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?",
            [(new, old) for old, new in moves],
        )
        self._conn.commit()

    def delete_by_paths(self, file_paths: list[str]) -> None:
        """Batch-delete songs by file path."""
        self._conn.executemany(
            "DELETE FROM songs WHERE file_path = ?",
            [(p,) for p in file_paths],
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Single-record write operations (used by UI-triggered CRUD)
    # ------------------------------------------------------------------

    def delete_by_path(self, file_path: str) -> None:
        """Delete a single song by file path (UI-triggered delete)."""
        self._conn.execute("DELETE FROM songs WHERE file_path = ?", (file_path,))
        self._conn.commit()

    def update_path(self, old_path: str, new_path: str) -> None:
        """Update a single song's file path (UI-triggered rename)."""
        self._conn.execute(
            "UPDATE songs SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?",
            (new_path, old_path),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def check_integrity(self) -> tuple[bool, str]:
        """Run PRAGMA integrity_check. Returns (ok, message)."""
        result = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok", result

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
