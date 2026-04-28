"""Song library management: scan, delete, rename, and display name operations."""

import contextlib
import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Callable

from pikaraoke.lib.get_platform import is_windows
from pikaraoke.lib.karaoke_database import (
    LYRICS_PROVENANCE_AUTO_LINE,
    LYRICS_PROVENANCE_AUTO_WORD,
    LYRICS_PROVENANCE_USER,
    KaraokeDatabase,
)
from pikaraoke.lib.library_scanner import build_song_record
from pikaraoke.lib.lyrics import ASS_MARKER
from pikaraoke.lib.metadata_parser import regex_tidy, youtube_id_suffix
from pikaraoke.lib.song_list import SongList

# Characters illegal in Windows filenames
_WINDOWS_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')

STEMS_CACHE_DIR_ROLE = "stems_cache_dir"
ASS_USER_ROLE = "ass_user"
ASS_AUTO_ROLE = "ass_auto"


def sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in filenames on the current platform."""
    if is_windows():
        name = _WINDOWS_ILLEGAL_CHARS.sub("-", name)
    return name.strip()


def _classify_ass(path: str) -> tuple[str, str]:
    """Inspect an .ass file and return (role, lyrics_provenance).

    Reads the first ~8 KB - enough to cover the [Script Info] / [V4+ Styles]
    blocks plus the first few Dialogue events. The presence of ``\\k`` in the
    body distinguishes word-level (auto_word) from line-level (auto_line)
    PiKaraoke output. Files without ``ASS_MARKER`` are user-authored.
    """
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(8192)
    except OSError:
        # Unreadable: safer to assume user-owned so cleanup doesn't nuke it.
        return ASS_USER_ROLE, LYRICS_PROVENANCE_USER
    if ASS_MARKER not in head:
        return ASS_USER_ROLE, LYRICS_PROVENANCE_USER
    provenance = LYRICS_PROVENANCE_AUTO_WORD if "\\k" in head else LYRICS_PROVENANCE_AUTO_LINE
    return ASS_AUTO_ROLE, provenance


def discover_song_artifacts(song_path: str) -> list[dict]:
    """Walk the song's directory and classify every sibling file by role.

    The primary media file itself is always included as ``primary_media``.
    The stems cache dir is not included here (it's registered by the stream
    manager once the sha256 is known).
    """
    directory = os.path.dirname(song_path) or "."
    stem = os.path.splitext(os.path.basename(song_path))[0]
    stem_lower = stem.lower()
    try:
        entries = os.listdir(directory)
    except OSError:
        return [{"role": "primary_media", "path": song_path}]

    artifacts: list[dict] = [{"role": "primary_media", "path": song_path}]
    for name in entries:
        full = os.path.join(directory, name)
        if full == song_path:
            continue
        name_lower = name.lower()
        base_lower, ext_lower = os.path.splitext(name_lower)
        # Exact-stem siblings (same basename, different extension).
        if base_lower == stem_lower:
            if ext_lower == ".m4a":
                artifacts.append({"role": "audio_source", "path": full})
            elif ext_lower == ".cdg":
                artifacts.append({"role": "cdg", "path": full})
            elif ext_lower == ".ass":
                role, provenance = _classify_ass(full)
                artifacts.append({"role": role, "path": full, "lyrics_provenance": provenance})
        # Multi-dot stems: <stem>.info.json, <stem>.<lang>.vtt, <stem>.cover.jpg
        elif name_lower.startswith(stem_lower + "."):
            if name_lower.endswith(".info.json"):
                artifacts.append({"role": "info_json", "path": full})
            elif ext_lower == ".vtt":
                artifacts.append({"role": "vtt", "path": full})
            elif name_lower.endswith((".cover.jpg", ".cover.jpeg", ".cover.png", ".cover.webp")):
                artifacts.append({"role": "cover_art", "path": full})
    return artifacts


def _consume_info_json(song_path: str, db: KaraokeDatabase, song_id: int) -> None:
    """Delete <stem>.info.json from disk and drop its artifact row.

    Called by ``register_download`` after info.json has been seeded into the
    songs table. Scanner-discovered songs (user-owned collections) skip this
    path so the original yt-dlp metadata stays on disk.
    """
    info_path = f"{os.path.splitext(song_path)[0]}.info.json"
    if os.path.exists(info_path):
        try:
            os.unlink(info_path)
        except OSError as e:
            logging.warning("failed to remove %s: %s", info_path, e)
    try:
        db.delete_artifacts_by_role(song_id, "info_json")
    except Exception:
        logging.exception("failed to unregister info_json artifact for song_id=%s", song_id)


def _track_metadata_from_info_json(song_path: str) -> dict:
    """Extract track metadata from <stem>.info.json for DB seeding.

    Includes artist + title + duration + source URL + language. ``track``
    and ``artist`` fall back to parsing "Artist - Track" out of the video
    title when yt-dlp lacks dedicated fields (common for non-music uploads).
    Returns an empty dict when the info.json is missing or unparseable.
    """
    info_path = f"{os.path.splitext(song_path)[0]}.info.json"
    if not os.path.exists(info_path):
        return {}
    try:
        with open(info_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    fields = {}
    track = (data.get("track") or "").strip()
    artist = (data.get("artist") or "").strip()
    if not track or not artist:
        video_title = (data.get("title") or "").strip()
        if " - " in video_title:
            left, right = video_title.split(" - ", 1)
            artist = artist or left.strip()
            track = track or right.strip()
    if artist:
        fields["artist"] = artist
    if track:
        fields["title"] = track
    if data.get("duration") is not None:
        fields["duration_seconds"] = float(data["duration"])
    url = data.get("webpage_url") or data.get("original_url")
    if url:
        fields["source_url"] = url
    if data.get("language"):
        fields["language"] = data["language"]
    return fields


class SongManager:
    """Manages the song library and file operations.

    Owns the SongList instance and provides all song discovery,
    delete, rename, and display name operations.
    """

    def __init__(
        self,
        download_path: str,
        db: KaraokeDatabase,
        get_title_tidy: Callable[[], bool] | None = None,
        enrich_on_download: bool = True,
    ) -> None:
        self.download_path = download_path
        self.songs = SongList()
        self._db = db
        self._get_title_tidy = get_title_tidy
        # Tests and anyone wanting to avoid the background iTunes/MusicBrainz
        # network calls can disable it; the enricher otherwise fires on every
        # register_download.
        self._enrich_on_download = enrich_on_download

    @staticmethod
    def filename_from_path(
        file_path: str, remove_youtube_id: bool = True, tidy: bool = True
    ) -> str:
        """Extract a display name from a file path.

        Args:
            file_path: Full path to the file.
            remove_youtube_id: Strip YouTube ID suffix if present.
            tidy: Apply regex_tidy() to strip noise words and normalize.

        Returns:
            Filename without extension, optionally cleaned.
        """
        name = os.path.splitext(os.path.basename(file_path))[0]
        suffix = youtube_id_suffix(file_path)
        if remove_youtube_id and suffix:
            name = name[: -len(suffix)]
        if tidy and suffix and remove_youtube_id:
            tidied = regex_tidy(name)
            if tidied:
                name = tidied
        return name

    def display_name_from_path(self, file_path: str, remove_youtube_id: bool = True) -> str:
        """Extract display name from path, respecting the enable_title_tidy preference."""
        tidy = self._get_title_tidy() if self._get_title_tidy else True
        return self.filename_from_path(file_path, remove_youtube_id=remove_youtube_id, tidy=tidy)

    def delete(self, song_path: str) -> None:
        """Delete a song from disk, SongList, and DB, plus every registered artifact.

        The artifacts table is authoritative: each row's file is unlinked.
        User-authored .ass files (role 'ass_user') are preserved. The shared
        demucs cache dir is only rmtree'd when no other song still references
        the same audio sha256.
        """
        logging.info(f"Deleting song: {song_path}")
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is not None:
            for artifact in self._db.get_artifacts(song_id):
                role, path = artifact["role"], artifact["path"]
                if role == STEMS_CACHE_DIR_ROLE:
                    sha = os.path.basename(path)
                    if self._db.count_songs_by_sha256(sha) <= 1:
                        shutil.rmtree(path, ignore_errors=True)
                elif role == ASS_USER_ROLE:
                    continue
                elif role == "primary_media":
                    # Handled separately below so we still unlink when no DB
                    # row exists (defensive path).
                    continue
                else:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(song_path)
        self.songs.remove(song_path)
        # song_artifacts rows cascade via ON DELETE CASCADE.
        self._db.delete_by_path(song_path)

    def rename(self, song_path: str, new_name: str) -> str:
        """Rename a song on disk, in SongList, and in DB. Returns new path.

        Artifact paths that share the old stem are rewritten. The stems
        cache dir is content-addressed (sha256), so its artifact row is
        preserved unchanged. The ``audio_source`` / ``cdg`` / ``ass_*`` / etc.
        companions are renamed on disk and their rows rewritten.
        """
        new_name = sanitize_filename(new_name)
        logging.info(f"Renaming song: '{song_path}' to: {new_name}")
        old_stem = os.path.splitext(os.path.basename(song_path))[0]
        _, ext = os.path.splitext(song_path)
        new_path = os.path.join(self.download_path, new_name + ext)

        song_id = self._db.get_song_id_by_path(song_path)
        artifacts = self._db.get_artifacts(song_id) if song_id is not None else []

        os.rename(song_path, new_path)

        new_artifacts: list[dict] = []
        for artifact in artifacts:
            role, old_path = artifact["role"], artifact["path"]
            if role == STEMS_CACHE_DIR_ROLE:
                new_artifacts.append({"role": role, "path": old_path})
                continue
            if role == "primary_media":
                new_artifacts.append({"role": role, "path": new_path})
                continue
            old_name = os.path.basename(old_path)
            old_base, old_ext = os.path.splitext(old_name)
            # Exact-stem match (base == old_stem, ext differs) OR multi-dot stem
            # (old_name starts with "<old_stem>."). Guards against false prefix
            # hits between 'song1' and 'song1_remix' siblings.
            if old_base == old_stem:
                new_companion_name = new_name + old_ext
            elif old_name.startswith(old_stem + "."):
                new_companion_name = new_name + old_name[len(old_stem) :]
            else:
                # Out-of-stem path (unusual) -- keep as-is.
                new_artifacts.append({"role": role, "path": old_path})
                continue
            new_companion = os.path.join(self.download_path, new_companion_name)
            with contextlib.suppress(FileNotFoundError):
                os.rename(old_path, new_companion)
            new_artifacts.append({"role": role, "path": new_companion})

        self.songs.rename(song_path, new_path)
        self._db.update_path(song_path, new_path)
        if song_id is not None:
            self._db.replace_artifacts(song_id, new_artifacts)
        return new_path

    def register_download(self, song_path: str) -> None:
        """Register a newly downloaded song in SongList and DB.

        Inserts rows for companion files (audio source, cdg, vtt, info.json),
        backfills track metadata from the info.json when available, and
        kicks off a best-effort iTunes+MusicBrainz enrichment in a
        background thread so the 3-6s of external network latency doesn't
        block the download pipeline.

        The info.json is consumed-then-deleted here: yt-dlp wrote it for
        this pipeline's benefit, everything useful has just been copied to
        the ``songs`` row, and downstream consumers (song_enricher,
        LyricsService) now read track metadata straight from the DB. The
        scanner backfill path (``library_scanner.LibraryScanner._backfill_artifacts``)
        deliberately does NOT delete info.json — it treats user-placed
        collections as external data that must not be mutated.
        """
        self.songs.add_if_valid(song_path)
        self._db.insert_songs([build_song_record(song_path)])
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        self._db.upsert_artifacts(song_id, discover_song_artifacts(song_path))
        meta = _track_metadata_from_info_json(song_path)
        if meta:
            self._db.update_track_metadata_with_provenance(song_id, "youtube", meta)
        _consume_info_json(song_path, self._db, song_id)
        if self._enrich_on_download:
            self._start_enrichment(song_id, song_path)

    def _start_enrichment(self, song_id: int, song_path: str) -> None:
        """Run iTunes + MusicBrainz enrichment in a daemon thread.

        Imports are deferred so the SongManager module stays lightweight for
        tests that don't care about enrichment and so the ``requests``
        import chain only happens when enrichment actually runs.
        """

        def _run() -> None:
            try:
                from pikaraoke.lib.song_enricher import enrich_song

                enrich_song(self._db, song_id, song_path)
            except Exception:
                logging.exception("enrichment failed for %s", song_path)

        threading.Thread(
            target=_run, name=f"enrich-{os.path.basename(song_path)}", daemon=True
        ).start()

    # ------------------------------------------------------------------
    # Cache lifecycle helpers used by stream_manager / lyrics
    # ------------------------------------------------------------------

    def register_stems_cache_dir(self, song_id: int, sha256: str) -> None:
        """Register ~/.pikaraoke-cache/<sha256>/ as this song's stems cache dir."""
        # Late import: demucs_processor pulls torch.
        from pikaraoke.lib.demucs_processor import CACHE_DIR

        self._db.upsert_artifacts(
            song_id,
            [{"role": STEMS_CACHE_DIR_ROLE, "path": os.path.join(CACHE_DIR, sha256)}],
        )

    def register_ass(self, song_id: int, ass_path: str, role: str) -> None:
        """Register an auto or user .ass file."""
        self._db.upsert_artifacts(song_id, [{"role": role, "path": ass_path}])
