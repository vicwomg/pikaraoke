"""SQLite database layer for persistent song library storage."""

import os
import sqlite3
import threading

from pikaraoke.lib.get_platform import get_data_directory

_SCHEMA_V1 = """
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

# v1 -> v2: audio fingerprint, processing config, track metadata, external IDs,
# and a song_artifacts table so SongManager.delete can unlink every file
# belonging to a song in one pass.
_MIGRATION_V2 = """
ALTER TABLE songs ADD COLUMN audio_sha256 TEXT;
ALTER TABLE songs ADD COLUMN audio_mtime  REAL;
ALTER TABLE songs ADD COLUMN audio_size   INTEGER;

ALTER TABLE songs ADD COLUMN demucs_model  TEXT;
ALTER TABLE songs ADD COLUMN aligner_model TEXT;
ALTER TABLE songs ADD COLUMN lyrics_source TEXT;

ALTER TABLE songs ADD COLUMN duration_seconds REAL;
ALTER TABLE songs ADD COLUMN source_url       TEXT;
ALTER TABLE songs ADD COLUMN language         TEXT;
ALTER TABLE songs ADD COLUMN album            TEXT;
ALTER TABLE songs ADD COLUMN track_number     INTEGER;
ALTER TABLE songs ADD COLUMN release_date     TEXT;

ALTER TABLE songs ADD COLUMN itunes_id                TEXT;
ALTER TABLE songs ADD COLUMN musicbrainz_recording_id TEXT;
ALTER TABLE songs ADD COLUMN isrc                     TEXT;

CREATE TABLE IF NOT EXISTS song_artifacts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    role    TEXT    NOT NULL,
    path    TEXT    NOT NULL,
    UNIQUE(song_id, path)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_song_id  ON song_artifacts(song_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_path     ON song_artifacts(path);
CREATE INDEX IF NOT EXISTS idx_songs_audio_sha256 ON songs(audio_sha256);
CREATE INDEX IF NOT EXISTS idx_songs_itunes_id    ON songs(itunes_id);
CREATE INDEX IF NOT EXISTS idx_songs_mbid         ON songs(musicbrainz_recording_id);
CREATE INDEX IF NOT EXISTS idx_songs_isrc         ON songs(isrc);
"""

_SCHEMA_VERSION = 2

_TRACK_METADATA_FIELDS = (
    "duration_seconds",
    "source_url",
    "language",
    "album",
    "track_number",
    "release_date",
    "itunes_id",
    "musicbrainz_recording_id",
    "isrc",
    "artist",
    "title",
    "year",
    "genre",
    "variant",
)


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
        # ON DELETE CASCADE on song_artifacts only fires with FKs enabled; the
        # SQLite default is OFF.
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _create_schema(self) -> None:
        self._conn.executescript(_SCHEMA_V1)
        with self._conn:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                self._conn.executescript(_MIGRATION_V2)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

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

    def get_song_by_path(self, file_path: str) -> sqlite3.Row | None:
        """Return the full song row for a path, or None if not present."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM songs WHERE file_path = ?", (file_path,)
            ).fetchone()

    def get_song_by_id(self, song_id: int) -> sqlite3.Row | None:
        """Return the full song row for an id, or None if not present."""
        with self._lock:
            return self._conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()

    def get_song_id_by_path(self, file_path: str) -> int | None:
        """Return the id of the song row for a path, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM songs WHERE file_path = ?", (file_path,)
            ).fetchone()
            return row[0] if row else None

    def get_songs_without_artifacts(self) -> list[tuple[int, str]]:
        """Return [(song_id, file_path)] for songs that have no artifact rows yet.

        Used by LibraryScanner to backfill artifacts after the v1 -> v2
        migration and for newly-inserted songs.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.id, s.file_path
                FROM songs s
                LEFT JOIN song_artifacts a ON a.song_id = s.id
                WHERE a.id IS NULL
                """
            ).fetchall()
            return [(r[0], r[1]) for r in rows]

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
    # Artifacts (files that belong to a song)
    # ------------------------------------------------------------------

    def upsert_artifacts(self, song_id: int, artifacts: list[dict]) -> None:
        """Insert or replace artifact rows keyed by (song_id, path).

        Each entry is {"role": str, "path": str}. Re-registering the same
        path with a different role updates the role in place.
        """
        if not artifacts:
            return
        rows = [(song_id, a["role"], a["path"]) for a in artifacts]
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO song_artifacts (song_id, role, path)
                VALUES (?, ?, ?)
                ON CONFLICT(song_id, path) DO UPDATE SET role = excluded.role
                """,
                rows,
            )

    def get_artifacts(self, song_id: int) -> list[sqlite3.Row]:
        """Return all artifact rows for a song."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, song_id, role, path FROM song_artifacts WHERE song_id = ?",
                (song_id,),
            ).fetchall()

    def delete_artifact(self, song_id: int, path: str) -> None:
        """Remove one artifact row by (song_id, path)."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM song_artifacts WHERE song_id = ? AND path = ?",
                (song_id, path),
            )

    def delete_artifacts_by_role(self, song_id: int, role: str) -> None:
        """Remove all artifacts for a song that match a given role."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM song_artifacts WHERE song_id = ? AND role = ?",
                (song_id, role),
            )

    def replace_artifacts(self, song_id: int, artifacts: list[dict]) -> None:
        """Replace the full artifact set for a song in one transaction."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM song_artifacts WHERE song_id = ?", (song_id,))
            if artifacts:
                self._conn.executemany(
                    "INSERT INTO song_artifacts (song_id, role, path) VALUES (?, ?, ?)",
                    [(song_id, a["role"], a["path"]) for a in artifacts],
                )

    # ------------------------------------------------------------------
    # Audio fingerprint + processing config
    # ------------------------------------------------------------------

    def count_songs_by_sha256(self, sha256: str) -> int:
        """Return how many songs share the given audio sha256 (ref count).

        Used before wiping ~/.pikaraoke-cache/<sha256>/ so we don't delete
        stems another song still depends on.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM songs WHERE audio_sha256 = ?", (sha256,)
            ).fetchone()[0]

    def update_audio_fingerprint(self, song_id: int, mtime: float, size: int, sha256: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE songs
                SET audio_mtime = ?, audio_size = ?, audio_sha256 = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (mtime, size, sha256, song_id),
            )

    def clear_audio_fingerprint(self, song_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE songs
                SET audio_mtime = NULL, audio_size = NULL, audio_sha256 = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (song_id,),
            )

    def update_processing_config(
        self,
        song_id: int,
        *,
        demucs_model: str | None = None,
        aligner_model: str | None = None,
        lyrics_source: str | None = None,
    ) -> None:
        """Record which models/sources produced the current cached artifacts.

        Only non-None arguments are written. The stems cache key effectively
        becomes (audio_sha256, demucs_model); lyrics is (audio_sha256,
        aligner_model, lyrics_source).
        """
        updates = []
        params: list = []
        if demucs_model is not None:
            updates.append("demucs_model = ?")
            params.append(demucs_model)
        if aligner_model is not None:
            updates.append("aligner_model = ?")
            params.append(aligner_model)
        if lyrics_source is not None:
            updates.append("lyrics_source = ?")
            params.append(lyrics_source)
        if not updates:
            return
        params.append(song_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE songs SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )

    def update_track_metadata(self, song_id: int, **fields) -> None:
        """Update any subset of cached music-metadata fields.

        Accepted keys: duration_seconds, source_url, language, album,
        track_number, release_date, itunes_id, musicbrainz_recording_id,
        isrc, artist, title, year, genre, variant. Unknown keys raise
        ValueError; keys whose values are None are skipped.
        """
        unknown = set(fields) - set(_TRACK_METADATA_FIELDS)
        if unknown:
            raise ValueError(f"update_track_metadata: unknown fields {sorted(unknown)}")
        cols = [k for k, v in fields.items() if v is not None]
        if not cols:
            return
        params = [fields[k] for k in cols] + [song_id]
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE songs SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )

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
