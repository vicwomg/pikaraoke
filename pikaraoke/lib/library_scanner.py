"""Filesystem scanner that synchronises the song directory with the database."""

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.metadata_parser import youtube_id_suffix
from pikaraoke.lib.song_list import SongList

_VALID_EXTENSIONS = SongList.VALID_EXTENSIONS


def build_song_record(file_path: str, files_in_dir: set[str] | None = None) -> dict:
    """Construct a song dict ready for KaraokeDatabase.insert_songs().

    Inspects the file's directory for companion files (.cdg, .ass) to
    determine the correct format.

    Args:
        file_path: Full path to the song file.
        files_in_dir: Pre-cached directory listing. When None, os.listdir
            is called (convenient for single-file registration).
    """
    if files_in_dir is None:
        try:
            files_in_dir = set(os.listdir(os.path.dirname(file_path)))
        except OSError:
            files_in_dir = set()
    return {
        "file_path": file_path,
        "youtube_id": _extract_youtube_id(file_path),
        "format": _detect_format(file_path, files_in_dir),
    }


def _extract_youtube_id(file_path: str) -> str | None:
    """Extract YouTube ID from PiKaraoke (---ID) or yt-dlp ([ID]) format."""
    suffix = youtube_id_suffix(file_path)
    if not suffix:
        return None
    # suffix is '---<ID>' or ' [<ID>]'; extract the 11-char ID after delimiter
    match = re.search(r"(?:---|\[)([A-Za-z0-9_-]{11})", suffix)
    return match.group(1) if match else None


def _detect_format(file_path: str, files_in_dir: set[str]) -> str:
    """Detect the song format, checking for companion files (.cdg, .ass)."""
    base, ext = os.path.splitext(os.path.basename(file_path))
    ext = ext.lower()
    dir_lower = {f.lower(): f for f in files_in_dir}
    if ext == ".mp3" and (base.lower() + ".cdg") in dir_lower:
        return "cdg"
    if ext == ".mp4" and (base.lower() + ".ass") in dir_lower:
        return "ass"
    return ext.lstrip(".")


@dataclass
class ScanResult:
    added: int
    moved: int
    deleted: int
    circuit_tripped: bool


class LibraryScanner:
    """Scans the song directory and synchronises it with the KaraokeDatabase.

    Handles filename-based move detection and a circuit breaker to protect
    against mass deletion when the song drive is unmounted.
    """

    CIRCUIT_BREAKER_THRESHOLD = 0.5
    _METADATA_KEY = "last_scan_directory"

    def __init__(self, db: KaraokeDatabase) -> None:
        self._db = db

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
                for p in paths:
                    records.append(build_song_record(p, files_in_dir))
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

        return ScanResult(
            added=len(to_insert),
            moved=len(moves),
            deleted=deleted,
            circuit_tripped=circuit_tripped,
        )

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
