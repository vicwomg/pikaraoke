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
import hashlib
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


def ensure_audio_fingerprint(db: KaraokeDatabase, song_id: int, audio_path: str) -> str | None:
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


def ensure_stems_config(db: KaraokeDatabase, song_id: int, current_demucs_model: str) -> bool:
    """Invalidate stems when the recorded demucs_model differs from the current one.

    Also cascades to the auto .ass: whisper alignment runs on stem output, so
    a demucs_model change means the existing .ass was aligned to stems from
    the wrong separator — drop it too (US-31). Previously the .ass
    invalidation depended on ``ensure_lyrics_config`` being called separately,
    which is easy to miss in callers that only prime stems.

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
        "demucs_model changed for song %d (%s -> %s); invalidating stems + auto .ass",
        song_id,
        cached,
        current_demucs_model,
    )
    _invalidate_stems(db, song_id, row["audio_sha256"])
    _invalidate_auto_ass(db, song_id)
    return False


def ensure_lyrics_config(
    db: KaraokeDatabase,
    song_id: int,
    current_aligner_model: str | None,
    current_demucs_model: str | None = None,
    current_lyrics_sha: str | None = None,
) -> bool:
    """Invalidate auto .ass when any dependency changed.

    Whisper alignment is downstream of both the demucs stems it runs on and
    the LRC text it aligns to, so any of three signals can stale the cache:

    * ``aligner_model``: whisper model swap (e.g. base -> large).
    * ``demucs_model``: stems regenerated from a different separation model.
    * ``lyrics_sha``: LRCLib returned different content for the same song.

    NULL-cached fields are treated as "not yet recorded" and never trigger
    invalidation, symmetric to ``ensure_stems_config``. Caller records the
    current values after the .ass successfully lands.
    """
    row = db.get_song_by_id(song_id)
    if row is None:
        return True
    stale_reason: str | None = None
    cached_aligner = row["aligner_model"]
    if cached_aligner is not None and cached_aligner != current_aligner_model:
        stale_reason = f"aligner_model changed ({cached_aligner} -> {current_aligner_model})"
    elif current_demucs_model is not None:
        cached_demucs = row["demucs_model"]
        if cached_demucs is not None and cached_demucs != current_demucs_model:
            stale_reason = f"demucs_model changed ({cached_demucs} -> {current_demucs_model})"
    if stale_reason is None and current_lyrics_sha is not None:
        cached_sha = row["lyrics_sha"]
        if cached_sha is not None and cached_sha != current_lyrics_sha:
            stale_reason = f"lyrics_sha changed ({cached_sha[:12]} -> {current_lyrics_sha[:12]})"
    if stale_reason is None:
        return True
    logger.info("invalidating auto .ass for song %d: %s", song_id, stale_reason)
    _invalidate_auto_ass(db, song_id)
    return False


def _hash_file_sha256(path: str) -> str | None:
    """Streaming sha256 of a file; None on read failure."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def ensure_artifact_fingerprint(db: KaraokeDatabase, song_id: int, path: str) -> str | None:
    """Return the current sha256 of one artifact file (US-30).

    Mirrors ``ensure_audio_fingerprint`` but per-artifact: on stat-match with
    the DB, trust the recorded sha; otherwise recompute and persist. Returns
    None when the artifact file is missing or unreadable — callers treat that
    as "fingerprint unknown" rather than erroring. No invalidation side
    effects: the caller decides what to do when the sha changes (artifact
    invalidation policy is per-role).
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    mtime, size = st.st_mtime, st.st_size

    rows = [a for a in db.get_artifacts(song_id) if a["path"] == path]
    if not rows:
        return None
    row = rows[0]
    try:
        cached_sha = row["sha256"]
        cached_mtime = row["mtime"]
        cached_size = row["size"]
    except (KeyError, IndexError):
        cached_sha = cached_mtime = cached_size = None
    if cached_sha and cached_mtime == mtime and cached_size == size:
        return cached_sha

    new_sha = _hash_file_sha256(path)
    if new_sha is None:
        return None
    db.update_artifact_fingerprint(song_id, path, mtime, size, new_sha)
    return new_sha


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
    """Unlink auto-generated .ass files (marker-tagged). Preserves ass_user rows.

    Also clears ``lyrics_sha`` so the next pipeline run treats LRCLib as
    "never fetched" and re-queries it. Without this, audio-sha invalidation
    deletes the .ass on disk but ``ensure_lyrics_config`` still sees a
    matching cached sha and never triggers a re-fetch — the waterfall
    diagram in US-31 (source audio changed -> re-fetch LRCLib) is broken.
    """
    for artifact in db.get_artifacts(song_id):
        if artifact["role"] != ASS_AUTO_ROLE:
            continue
        path = artifact["path"]
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        db.delete_artifact(song_id, path)
    db.update_processing_config(song_id, lyrics_sha=None, aligner_model=None)
