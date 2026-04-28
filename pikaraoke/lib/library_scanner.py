"""Filesystem scanner that synchronises the song directory with the database."""

import contextlib
import logging
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.metadata_parser import youtube_id_suffix
from pikaraoke.lib.song_list import SongList

_VALID_EXTENSIONS = SongList.VALID_EXTENSIONS

# Roles whose loss/content-change should trigger a fresh lyrics pipeline run.
# Cosmetic artifacts (cover_art, info_json, vtt) drop quietly; primary_media
# is already handled by the main scan diff (deletion of the song row).
_LYRICS_TRIGGER_ROLES = frozenset({"ass_auto", "audio_source", "primary_media"})

# Formats where auto-generated lyrics aren't expected: CDG ships its own
# graphic karaoke track, ZIP is a CDG bundle. mp3/mp4/ass all qualify.
_LYRICS_SKIP_FORMATS = frozenset({"cdg", "zip"})


def build_song_record(
    file_path: str,
    files_in_dir: set[str] | None = None,
    files_lower: set[str] | None = None,
) -> dict:
    """Construct a song dict ready for KaraokeDatabase.insert_songs().

    Inspects the file's directory for companion files (.cdg, .ass) to
    determine the correct format.

    Args:
        file_path: Full path to the song file.
        files_in_dir: Pre-cached directory listing. When None, os.listdir
            is called (convenient for single-file registration).
        files_lower: Pre-lowered filenames for companion detection. Built
            from files_in_dir when not provided.
    """
    if files_in_dir is None:
        try:
            files_in_dir = set(os.listdir(os.path.dirname(file_path)))
        except OSError:
            files_in_dir = set()
    if files_lower is None:
        files_lower = {f.lower() for f in files_in_dir}
    return {
        "file_path": file_path,
        "youtube_id": _extract_youtube_id(file_path),
        "format": _detect_format(file_path, files_lower),
    }


def _extract_youtube_id(file_path: str) -> str | None:
    """Extract YouTube ID from PiKaraoke (---ID) or yt-dlp ([ID]) format."""
    suffix = youtube_id_suffix(file_path)
    if not suffix:
        return None
    # suffix is '---<ID>' or ' [<ID>]'; strip delimiters to get the 11-char ID
    if suffix.startswith("---"):
        return suffix[3:]
    return suffix.strip(" []")


def _detect_format(file_path: str, files_lower: set[str]) -> str:
    """Detect the song format, checking for companion files (.cdg, .ass)."""
    base, ext = os.path.splitext(os.path.basename(file_path))
    ext = ext.lower()
    base_lower = base.lower()
    if ext == ".mp3" and (base_lower + ".cdg") in files_lower:
        return "cdg"
    if ext == ".mp4" and (base_lower + ".ass") in files_lower:
        return "ass"
    return ext.lstrip(".")


@dataclass
class ScanResult:
    added: int
    moved: int
    deleted: int
    circuit_tripped: bool
    # Songs whose artifacts indicate they need a fresh lyrics pipeline run
    # (missing or sha-mismatched ass_auto, content-changed audio source, or
    # an imported song that has never produced an .ass).
    reprocess_paths: list[str] = field(default_factory=list)


class LibraryScanner:
    """Scans the song directory and synchronises it with the KaraokeDatabase.

    Handles filename-based move detection and a circuit breaker to protect
    against mass deletion when the song drive is unmounted.
    """

    CIRCUIT_BREAKER_THRESHOLD = 0.5
    _METADATA_KEY = "last_scan_directory"

    def __init__(
        self,
        db: KaraokeDatabase,
        on_provenance_classified: Callable[[], None] | None = None,
    ) -> None:
        self._db = db
        # Optional callback fired right after _backfill_artifacts populates
        # lyrics_provenance for newly-classified .ass files. The startup
        # invalidator hangs off this seam so it sees those classifications
        # before _verify_integrity runs.
        self._on_provenance_classified = on_provenance_classified

    def scan(self, songs_dir: str) -> ScanResult:
        """Synchronise the database with the filesystem.

        Algorithm:
        1. Walk disk to collect current paths.
        2. Diff against DB paths to find new and gone files.
        3. Filename-based move detection: unambiguous basename matches are
           treated as moves rather than delete+insert.
        4. Circuit-breaker check: if >50% of truly missing songs (after
           accounting for moves), skip deletes — unless the scan directory
           changed, in which case the breaker is bypassed.
        5. Apply path updates (moves), inserts, and deletes to the DB.
        """
        last_dir = self._db.get_metadata(self._METADATA_KEY)

        disk_paths = self._walk_disk(songs_dir)
        logging.info(f"Scan: found {len(disk_paths)} song(s) on disk")
        db_paths = set(self._db.get_all_song_paths())

        # Detect directory change. If metadata exists, compare directly.
        # If not (first scan after upgrade), infer from whether any DB paths
        # fall under the current scan directory.
        if last_dir is not None:
            directory_changed = os.path.normcase(last_dir) != os.path.normcase(songs_dir)
        elif db_paths:
            prefix = os.path.normcase(songs_dir + os.sep)
            directory_changed = not any(os.path.normcase(p).startswith(prefix) for p in db_paths)
        else:
            directory_changed = False

        new_on_disk = disk_paths - db_paths
        gone_from_disk = db_paths - disk_paths

        moves = self._detect_moves(gone_from_disk, new_on_disk)
        moved_old = {old for old, _ in moves}
        moved_new = {new for _, new in moves}

        to_insert = new_on_disk - moved_new
        to_delete = gone_from_disk - moved_old

        # Circuit breaker evaluates truly missing songs (after move detection),
        # so relocated files don't falsely trigger it.
        # Bypass when the scan directory changed — the user intentionally moved.
        if directory_changed:
            circuit_tripped = False
            if to_delete:
                logging.info(
                    f"Scan directory changed ({last_dir} -> {songs_dir}), "
                    f"bypassing circuit breaker for {len(to_delete)} deletion(s)"
                )
        else:
            circuit_tripped = self._check_circuit_breaker(len(to_delete), len(db_paths))

        # Cache directory listings so os.listdir is called once per directory
        # instead of once per file (companion file detection needs the listing).
        if to_insert:
            by_dir: dict[str, list[str]] = defaultdict(list)
            for p in to_insert:
                by_dir[os.path.dirname(p)].append(p)
            records = []
            for dirpath, paths in by_dir.items():
                try:
                    files_in_dir = set(os.listdir(dirpath))
                except OSError:
                    files_in_dir = set()
                files_lower = {f.lower() for f in files_in_dir}
                for p in paths:
                    records.append(build_song_record(p, files_in_dir, files_lower))
        else:
            records = []
        deletes = list(to_delete) if to_delete and not circuit_tripped else []

        self._db.apply_scan_diff(moves, records, deletes)

        if moves:
            logging.info(f"Scan: moved {len(moves)} song(s)")
        if to_insert:
            logging.info(f"Scan: added {len(to_insert)} song(s)")
        deleted = len(deletes)
        if deleted:
            logging.info(f"Scan: deleted {deleted} song(s)")

        if last_dir != songs_dir:
            self._db.set_metadata(self._METADATA_KEY, songs_dir)

        # After v1 -> v2 migration, existing songs have no artifact rows.
        # Also handles songs just inserted above, so register_download-style
        # backfill runs once per song via a single pass.
        self._backfill_artifacts()

        # Stale-aligner sweep runs here so it sees any lyrics_provenance
        # values just stamped by _backfill_artifacts. Wired by Karaoke as
        # _invalidate_stale_alignments_from_db; no-op when not provided
        # (e.g. in scanner unit tests).
        if self._on_provenance_classified is not None:
            try:
                self._on_provenance_classified()
            except Exception:
                logging.exception("Scan: stale-alignment sweep raised")

        # Walk artifacts to spot files that vanished or changed sha out-of-band
        # (user edited an .ass, mounted volume regenerated stems, etc.) and
        # surface songs that need a fresh lyrics pipeline run.
        reprocess_paths = self._verify_integrity()
        if reprocess_paths:
            logging.info(f"Scan: {len(reprocess_paths)} song(s) flagged for reprocess")

        return ScanResult(
            added=len(to_insert),
            moved=len(moves),
            deleted=deleted,
            circuit_tripped=circuit_tripped,
            reprocess_paths=reprocess_paths,
        )

    def _verify_integrity(self) -> list[str]:
        """Validate every registered artifact and queue songs for reprocess.

        For each artifact row:

        * stat the file. Missing -> drop the row. If the role normally drives
          the lyrics pipeline (ass_auto / audio_source / primary_media), the
          song is queued for reprocess; cosmetic artifacts drop quietly.
        * Otherwise call ``verify_artifact_fingerprint`` (cheap stat-then-sha
          ladder). On a real content change, an ass_auto file is unlinked +
          its row dropped + lyrics_sha cleared via ``invalidate_auto_ass``;
          a changed audio source delegates to ``ensure_audio_fingerprint``
          which cascades stems + auto-ass invalidation; other roles just
          rebaseline.

        Songs that survive integrity but have no ass_auto/ass_user companion
        (typical of fresh scanner-imported collections) are also queued so
        the lyrics pipeline runs once for each.

        Imports are deferred to keep audio_fingerprint / demucs_processor
        (torch) out of the scanner's import graph.
        """
        from pikaraoke.lib.audio_fingerprint import (
            ARTIFACT_CHANGED,
            ARTIFACT_MISSING,
            ensure_audio_fingerprint,
            invalidate_auto_ass,
            verify_artifact_fingerprint,
        )
        from pikaraoke.lib.demucs_processor import resolve_audio_source

        queue: list[str] = []
        for song_id, song_path in self._db.get_all_song_ids_and_paths():
            if not os.path.exists(song_path):
                # Main scan already deletes vanished primary_media rows; if
                # we still see one, it means the circuit breaker held off
                # the delete. Don't queue reprocess for an unreachable song.
                continue

            artifacts = self._db.get_artifacts(song_id)
            roles_present: set[str] = set()
            needs_reprocess = False

            for artifact in artifacts:
                role = artifact["role"]
                path = artifact["path"]

                if role == "stems_cache_dir":
                    # Not a file — sha doesn't apply. Drop the row when the
                    # cache dir is gone; next play regenerates.
                    if not os.path.isdir(path):
                        self._db.delete_artifact(song_id, path)
                    else:
                        roles_present.add(role)
                    continue

                verdict = verify_artifact_fingerprint(
                    self._db,
                    song_id,
                    path,
                    recorded_sha=artifact["sha256"],
                    recorded_mtime=artifact["mtime"],
                    recorded_size=artifact["size"],
                )

                if verdict == ARTIFACT_MISSING:
                    self._db.delete_artifact(song_id, path)
                    if role in _LYRICS_TRIGGER_ROLES:
                        needs_reprocess = True
                    continue

                roles_present.add(role)

                if verdict == ARTIFACT_CHANGED:
                    if role == "ass_auto":
                        invalidate_auto_ass(self._db, song_id)
                        needs_reprocess = True
                    elif role in {"primary_media", "audio_source"}:
                        # Source bytes drifted: re-run the canonical
                        # invalidation cascade (stems + auto .ass).
                        with contextlib.suppress(Exception):
                            ensure_audio_fingerprint(
                                self._db, song_id, resolve_audio_source(song_path)
                            )
                        needs_reprocess = True
                    # ass_user / vtt / cdg / cover_art / info_json: rebaseline
                    # only — verify_artifact_fingerprint already did that.

            # Imported songs that never went through the lyrics pipeline
            # (no auto + no user-authored .ass) get one shot at it now.
            if (
                not needs_reprocess
                and "ass_auto" not in roles_present
                and "ass_user" not in roles_present
            ):
                row = self._db.get_song_by_path(song_path)
                if row is not None and row["format"] not in _LYRICS_SKIP_FORMATS:
                    needs_reprocess = True

            if needs_reprocess:
                queue.append(song_path)

        return queue

    def _backfill_artifacts(self) -> None:
        """Register artifacts + info.json metadata for songs that lack them.

        Also stamps ``lyrics_provenance`` on songs whose .ass file was just
        discovered: ``discover_song_artifacts`` reads each .ass head once
        and tags it ``auto_word`` (\\k present), ``auto_line`` (PiKaraoke
        marker only), or ``user`` (no marker). The startup sweep relies
        on this column to invalidate stale word-level files after a model
        bump - see ``KaraokeDatabase.get_song_ids_for_realignment``.

        Imports are deferred to avoid pulling song_manager (which imports
        lyrics) at module-import time.
        """
        missing = self._db.get_songs_without_artifacts()
        if not missing:
            return
        from pikaraoke.lib.song_manager import (
            _track_metadata_from_info_json,
            discover_song_artifacts,
        )

        logging.info(f"Scan: backfilling artifacts for {len(missing)} song(s)")
        for song_id, path in missing:
            artifacts = discover_song_artifacts(path)
            self._db.upsert_artifacts(song_id, artifacts)
            for artifact in artifacts:
                provenance = artifact.get("lyrics_provenance")
                if provenance:
                    self._db.update_processing_config(song_id, lyrics_provenance=provenance)
            meta = _track_metadata_from_info_json(path)
            if meta:
                # info.json is yt-dlp's output, so provenance = "youtube".
                # Media-specific fields rank YouTube above remote DBs; identity
                # fields (only ``language`` here) rank it below MB/iTunes so an
                # enrichment pass can still override.
                self._db.update_track_metadata_with_provenance(song_id, "youtube", meta)

    def _walk_disk(self, songs_dir: str) -> set[str]:
        """Walk the directory tree and collect paths of valid song files."""
        found: set[str] = set()
        for dirpath, _dirnames, filenames in os.walk(songs_dir):
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() in _VALID_EXTENSIONS:
                    found.add(os.path.join(dirpath, filename))
        return found

    def _detect_moves(self, gone: set[str], new: set[str]) -> list[tuple[str, str]]:
        """Match gone paths to new paths by basename (strict 1:1 only).

        A match is only accepted when exactly one old path and exactly one new
        path share the same basename. Karaoke filenames embed YouTube IDs so
        collisions are extremely rare in practice.
        """
        new_by_basename: dict[str, list[str]] = {}
        for path in new:
            new_by_basename.setdefault(os.path.basename(path), []).append(path)
        old_by_basename: dict[str, list[str]] = {}
        for path in gone:
            old_by_basename.setdefault(os.path.basename(path), []).append(path)

        moves: list[tuple[str, str]] = []
        for basename, old_paths in old_by_basename.items():
            new_paths = new_by_basename.get(basename, [])
            if len(old_paths) == 1 and len(new_paths) == 1:
                moves.append((old_paths[0], new_paths[0]))
        return moves

    def _check_circuit_breaker(self, gone_count: int, db_count: int) -> bool:
        """Return True if the deletion ratio exceeds the safe threshold."""
        if db_count == 0 or gone_count == 0:
            return False
        return (gone_count / db_count) > self.CIRCUIT_BREAKER_THRESHOLD
