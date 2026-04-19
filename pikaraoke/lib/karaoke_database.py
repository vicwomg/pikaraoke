"""SQLite database layer for persistent song library storage."""

import json
import os
import sqlite3
import threading

from pikaraoke.lib.get_platform import get_data_directory

# Confidence ladder for canonical music metadata (US-28). Higher number =
# higher trust. The enricher uses this to decide whether a newly-arrived
# value should replace an existing one. Sources not listed are treated as
# 0 (lowest), so unknown sources never override known ones.
METADATA_SOURCE_CONFIDENCE = {
    "scanner": 0,
    "youtube": 1,
    "itunes": 2,
    "musicbrainz": 3,
    "manual": 4,  # User edits override everything.
}

# Fields that prefer media-provenance over musicbrainz (the song-as-file,
# not the song-as-composition). YouTube wins for these.
_MEDIA_FIELDS = frozenset({"duration_seconds", "source_url", "youtube_id"})

_MEDIA_SOURCE_CONFIDENCE = {
    "scanner": 0,
    "musicbrainz": 1,
    "itunes": 2,
    "youtube": 3,
    "manual": 4,
}


def _confidence_for(field: str, source: str) -> int:
    """Return the confidence score for a (field, source) pair."""
    table = _MEDIA_SOURCE_CONFIDENCE if field in _MEDIA_FIELDS else METADATA_SOURCE_CONFIDENCE
    return table.get(source, 0)

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

# v2 -> v3: sha of the LRC used to produce auto .ass, so subtitle-content
# changes invalidate whisperx alignment (stems don't change; only the lyrics did).
_MIGRATION_V3 = """
ALTER TABLE songs ADD COLUMN lyrics_sha TEXT;
"""

# v3 -> v4: per-field provenance for canonical metadata. JSON dict mapping
# field name -> source identifier (e.g. {"artist": "musicbrainz", "title":
# "itunes"}). Lets the enricher do confidence-based override (higher-trust
# source replaces lower-trust value) instead of fill-if-NULL only.
_MIGRATION_V4 = """
ALTER TABLE songs ADD COLUMN metadata_sources TEXT;
"""

# v4 -> v5: per-artifact content fingerprints (US-30). Each cache file (.ass,
# .vtt, .info.json, stems dir, ...) gets its own sha256/size/mtime so content
# changes on one sibling don't require re-fingerprinting the whole song.
# Columns are nullable and back-filled lazily on first cheap-refresh call.
_MIGRATION_V5 = """
ALTER TABLE song_artifacts ADD COLUMN sha256 TEXT;
ALTER TABLE song_artifacts ADD COLUMN size   INTEGER;
ALTER TABLE song_artifacts ADD COLUMN mtime  REAL;
"""

_SCHEMA_VERSION = 5

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
            # Introspect the songs table to detect partially-applied migrations
            # (executescript auto-commits per-statement, so a crash between the
            # ALTER TABLEs and the PRAGMA user_version bump leaves the DB in a
            # mixed state where re-running the migration fails on duplicate
            # column). The presence of a migration's canary column is the
            # authoritative signal that it has already run.
            columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(songs)").fetchall()
            }
            if version < 2 and "audio_sha256" not in columns:
                self._conn.executescript(_MIGRATION_V2)
            if version < 3 and "lyrics_sha" not in columns:
                self._conn.executescript(_MIGRATION_V3)
            if version < 4 and "metadata_sources" not in columns:
                self._conn.executescript(_MIGRATION_V4)
            artifact_columns = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(song_artifacts)").fetchall()
            }
            if version < 5 and "sha256" not in artifact_columns:
                self._conn.executescript(_MIGRATION_V5)
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
                """
                SELECT id, song_id, role, path, sha256, size, mtime
                FROM song_artifacts WHERE song_id = ?
                """,
                (song_id,),
            ).fetchall()

    def update_artifact_fingerprint(
        self, song_id: int, path: str, mtime: float, size: int, sha256: str
    ) -> None:
        """Record the fingerprint of one artifact file (US-30)."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE song_artifacts
                SET sha256 = ?, size = ?, mtime = ?
                WHERE song_id = ? AND path = ?
                """,
                (sha256, size, mtime, song_id, path),
            )

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

    def stamp_enrichment_attempt(self, song_id: int, status: str, attempted_at: str) -> None:
        """Record metadata_status + attempt count for a song.

        Increments enrichment_attempts atomically. Used by the enricher so
        failed lookups are visible in the DB for later retry.
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE songs
                SET metadata_status = ?,
                    enrichment_attempts = COALESCE(enrichment_attempts, 0) + 1,
                    last_enrichment_attempt = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, attempted_at, song_id),
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

    # Sentinel that lets update_processing_config distinguish "caller didn't
    # pass this field" from "caller explicitly cleared it". Plain None means
    # clear (e.g. reverting to VTT-only), omission means keep-as-is.
    _UNSET = object()

    def update_processing_config(
        self,
        song_id: int,
        *,
        demucs_model: str | None = None,
        aligner_model: object = _UNSET,
        lyrics_source: str | None = None,
        lyrics_sha: object = _UNSET,
    ) -> None:
        """Record which models/sources produced the current cached artifacts.

        Only provided arguments are written. The stems cache key effectively
        becomes (audio_sha256, demucs_model); lyrics is (audio_sha256,
        aligner_model, lyrics_source, lyrics_sha). aligner_model and lyrics_sha
        accept explicit None to clear (e.g. LRCLib line-level has no aligner).
        """
        updates = []
        params: list = []
        if demucs_model is not None:
            updates.append("demucs_model = ?")
            params.append(demucs_model)
        if aligner_model is not self._UNSET:
            updates.append("aligner_model = ?")
            params.append(aligner_model)
        if lyrics_source is not None:
            updates.append("lyrics_source = ?")
            params.append(lyrics_source)
        if lyrics_sha is not self._UNSET:
            updates.append("lyrics_sha = ?")
            params.append(lyrics_sha)
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

        This bypasses provenance bookkeeping; prefer
        ``update_track_metadata_with_provenance`` for enrichment writes
        so the confidence ladder (US-28) applies.
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

    def get_metadata_sources(self, song_id: int) -> dict[str, str]:
        """Return the {field: source} provenance dict for a song.

        Empty dict when the column is NULL (pre-V4 row, or never enriched).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT metadata_sources FROM songs WHERE id = ?", (song_id,)
            ).fetchone()
        if row is None or row[0] is None:
            return {}
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def update_track_metadata_with_provenance(
        self, song_id: int, source: str, fields: dict
    ) -> dict[str, str]:
        """Confidence-aware metadata write (US-28).

        For each provided (field, value) pair:

        * If the field has no recorded source, write the value and stamp
          ``source``.
        * If ``source`` is at least as confident as the recorded source
          (per ``METADATA_SOURCE_CONFIDENCE`` / ``_MEDIA_SOURCE_CONFIDENCE``),
          overwrite the value and stamp the new source.
        * Otherwise leave the field alone.

        ``manual`` is sticky — once a field's source is ``manual``, only
        another ``manual`` write can replace it.

        Returns the *applied* {field: source} subset (useful for tests
        and for the caller's "did we change anything" check).
        """
        clean = {k: v for k, v in fields.items() if v is not None and v != ""}
        unknown = set(clean) - set(_TRACK_METADATA_FIELDS)
        if unknown:
            raise ValueError(
                f"update_track_metadata_with_provenance: unknown fields {sorted(unknown)}"
            )
        if not clean:
            return {}
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT metadata_sources FROM songs WHERE id = ?", (song_id,)
            ).fetchone()
            if row is None:
                return {}
            try:
                sources = json.loads(row[0]) if row[0] else {}
            except (TypeError, ValueError):
                sources = {}
            if not isinstance(sources, dict):
                sources = {}

            new_source_conf = {f: _confidence_for(f, source) for f in clean}
            applied: dict[str, str] = {}
            for field, value in clean.items():
                cached_source = sources.get(field)
                if cached_source is None:
                    applied[field] = value
                    sources[field] = source
                    continue
                cached_conf = _confidence_for(field, cached_source)
                if new_source_conf[field] >= cached_conf:
                    applied[field] = value
                    sources[field] = source
            if not applied:
                return {}
            cols = list(applied.keys())
            params = [applied[c] for c in cols]
            params.append(json.dumps(sources, sort_keys=True))
            params.append(song_id)
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            self._conn.execute(
                f"UPDATE songs SET {set_clause}, metadata_sources = ?, "
                f"updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )
        return {f: source for f in applied}

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
