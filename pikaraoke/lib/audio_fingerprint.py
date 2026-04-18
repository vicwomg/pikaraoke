"""Audio fingerprinting and cache invalidation for Demucs stems and auto lyrics.

Three-tier check before reading from the Demucs cache:
  1. stat(audio): if mtime + size match the DB, trust the cached sha256.
  2. else recompute sha256; if it matches, just refresh mtime/size (free metadata
     touch).
  3. else the source audio actually changed — wipe the old cache dir (ref-count
     safe) and drop auto-generated .ass, then record the new fingerprint.

Separately, stems / lyrics cache keys include the model identifier so switching
demucs_model or aligner_model auto-invalidates without touching audio bytes.
"""

import contextlib
import logging
import os
import shutil

from pikaraoke.lib.karaoke_database import KaraokeDatabase


def _demucs_bits():
    # Late import -- pulling demucs_processor at module import time would load
    # torch on every caller that imports audio_fingerprint.
    from pikaraoke.lib.demucs_processor import CACHE_DIR, get_cache_key

    return CACHE_DIR, get_cache_key

logger = logging.getLogger(__name__)

STEMS_CACHE_DIR_ROLE = "stems_cache_dir"
ASS_AUTO_ROLE = "ass_auto"


def ensure_audio_fingerprint(
    db: KaraokeDatabase, song_id: int, audio_path: str
) -> str | None:
    """Return the current audio sha256, invalidating stale caches if it changed.

    Returns None when the source file is missing (out-of-band deletion).
    """
    try:
        st = os.stat(audio_path)
    except OSError:
        return None
    mtime, size = st.st_mtime, st.st_size

    row = db.get_song_by_id(song_id)
    if row is None:
        return None

    cached_sha = row["audio_sha256"]
    if cached_sha and row["audio_mtime"] == mtime and row["audio_size"] == size:
        return cached_sha

    cache_dir, get_cache_key = _demucs_bits()
    new_sha = get_cache_key(audio_path)
    if new_sha == cached_sha:
        db.update_audio_fingerprint(song_id, mtime, size, new_sha)
        return new_sha

    if cached_sha:
        logger.info(
            "audio changed for song %d (%s -> %s); invalidating stems + auto lyrics",
            song_id,
            cached_sha[:12],
            new_sha[:12],
        )
        _invalidate_stems(db, song_id, cached_sha)
        _invalidate_auto_ass(db, song_id)

    db.update_audio_fingerprint(song_id, mtime, size, new_sha)
    db.upsert_artifacts(
        song_id,
        [{"role": STEMS_CACHE_DIR_ROLE, "path": os.path.join(cache_dir, new_sha)}],
    )
    return new_sha


def ensure_stems_config(
    db: KaraokeDatabase, song_id: int, current_demucs_model: str
) -> bool:
    """Invalidate stems when the recorded demucs_model differs from the current one.

    NULL means "not yet recorded" (e.g. first play, post-migration) and does
    NOT trigger invalidation. Does not write the current model — the caller
    records it after the stems successfully land on disk so the DB doesn't
    claim a model for a cache that isn't there yet.
    """
    row = db.get_song_by_id(song_id)
    if row is None:
        return True
    cached = row["demucs_model"]
    if cached is None or cached == current_demucs_model:
        return True
    logger.info(
        "demucs_model changed for song %d (%s -> %s); invalidating stems",
        song_id,
        cached,
        current_demucs_model,
    )
    _invalidate_stems(db, song_id, row["audio_sha256"])
    return False


def ensure_lyrics_config(
    db: KaraokeDatabase, song_id: int, current_aligner_model: str | None
) -> bool:
    """Invalidate auto .ass when the recorded aligner_model differs.

    Symmetric to ``ensure_stems_config``: NULL is a no-op and the model is
    recorded by the caller after the .ass actually lands.
    """
    row = db.get_song_by_id(song_id)
    if row is None:
        return True
    cached = row["aligner_model"]
    if cached is None or cached == current_aligner_model:
        return True
    logger.info(
        "aligner_model changed for song %d (%s -> %s); invalidating auto .ass",
        song_id,
        cached,
        current_aligner_model,
    )
    _invalidate_auto_ass(db, song_id)
    return False


def _invalidate_stems(db: KaraokeDatabase, song_id: int, old_sha: str | None) -> None:
    """Drop the stems cache dir for this song if it's the sole owner of the sha.

    Does NOT clear demucs_model on the songs row — the caller (ensure_stems_config
    or the next cache-warm) writes the new model identifier.
    """
    if old_sha and db.count_songs_by_sha256(old_sha) <= 1:
        cache_root, _ = _demucs_bits()
        shutil.rmtree(os.path.join(cache_root, old_sha), ignore_errors=True)
    db.delete_artifacts_by_role(song_id, STEMS_CACHE_DIR_ROLE)


def _invalidate_auto_ass(db: KaraokeDatabase, song_id: int) -> None:
    """Unlink auto-generated .ass files (marker-tagged). Preserves ass_user rows."""
    for artifact in db.get_artifacts(song_id):
        if artifact["role"] != ASS_AUTO_ROLE:
            continue
        path = artifact["path"]
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        db.delete_artifact(song_id, path)
