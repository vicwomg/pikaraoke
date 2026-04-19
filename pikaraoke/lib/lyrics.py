"""Auto-fetch synced lyrics from LRCLib and render as ASS subtitles.

Pipeline:
  song_downloaded event -> LyricsService.fetch_and_convert
    1. Read <stem>.info.json (written by yt-dlp --write-info-json)
    2. Query LRCLib for syncedLyrics
    3. Convert LRC to line-level ASS and write <stem>.ass
    4. (Optional) in a background thread, run forced alignment and
       replace the ASS with per-word \\k-tagged highlighting.

The existing .ass stack (FileResolver, SubtitlesOctopus in splash.js)
renders the output automatically - no UI changes required.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from threading import Thread
from typing import Protocol

import requests

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.music_metadata import resolve_metadata

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net"
LRCLIB_TIMEOUT = 5.0

# LRC timestamp: [mm:ss.xx] or [mm:ss.xxx] or [mm:ss]
_LRC_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")

# VTT cue timestamp line: `00:00:01.000 --> 00:00:03.000`.
_VTT_CUE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})"
)
# Inline tags like `<c>`, `</c>`, `<00:00:01.000>`, `<v Speaker>`.
_VTT_TAG = re.compile(r"<[^>]+>")

# Marker in [Script Info] used to distinguish auto-generated ASS from
# user-supplied Aegisub files. Auto-generated files may be overwritten
# on re-download; user files are left alone.
ASS_MARKER = "PiKaraoke Auto-Lyrics"

VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi")


@dataclass(frozen=True)
class Word:
    """A single word with its start/end time in seconds."""

    text: str
    start: float
    end: float


class Aligner(Protocol):
    """Produces word-level timings for a song given its audio and reference lyrics."""

    def align(
        self, audio_path: str, reference_text: str, language: str | None = None
    ) -> list[Word]:
        """``language`` is an optional hint that lets the aligner skip its own
        detection pass (e.g. when the caller already cached a prior result)."""
        ...

    @property
    def model_id(self) -> str:
        """Stable identifier recorded in the DB so model swaps invalidate cached .ass."""
        ...


class LyricsService:
    """Fetches synced lyrics from LRCLib and writes them as ASS subtitles."""

    def __init__(
        self,
        download_path: str,
        events: EventSystem,
        aligner: Aligner | None = None,
        db: KaraokeDatabase | None = None,
    ) -> None:
        self._download_path = download_path
        self._events = events
        self._aligner = aligner
        self._db = db

    def _register_ass(
        self,
        song_path: str,
        lyrics_source: str,
        aligner_model: str | None,
        lyrics_sha: str | None,
    ) -> None:
        """Record the written .ass in song_artifacts and stamp processing config.

        ``lyrics_sha`` fingerprints the LRC text that produced the .ass, so a
        later LRCLib refresh returning different content invalidates the cache.
        No-op when db is not wired or when the song is not in the DB.
        """
        if self._db is None:
            return
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        self._db.upsert_artifacts(song_id, [{"role": "ass_auto", "path": _ass_path(song_path)}])
        self._db.update_processing_config(
            song_id,
            lyrics_source=lyrics_source,
            aligner_model=aligner_model,
            lyrics_sha=lyrics_sha,
        )

    def _register_user_ass(self, song_path: str) -> None:
        if self._db is None:
            return
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        self._db.upsert_artifacts(song_id, [{"role": "ass_user", "path": _ass_path(song_path)}])

    def _maybe_drop_stale_auto_ass(self, song_path: str, lyrics_sha: str | None) -> None:
        """Delete the auto .ass when any upstream dependency changed.

        Invalidates on: aligner model swap, demucs model swap (whisper aligned
        to stems from the old model), or LRC content change (LRCLib updated
        the lyrics). Runs before re-generating lyrics so stale artifacts are
        not served.
        """
        if self._db is None:
            return
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        from pikaraoke.lib.audio_fingerprint import ensure_lyrics_config
        from pikaraoke.lib.demucs_processor import DEMUCS_MODEL

        aligner_id = self._aligner.model_id if self._aligner is not None else None
        ensure_lyrics_config(
            self._db,
            song_id,
            current_aligner_model=aligner_id,
            current_demucs_model=DEMUCS_MODEL,
            current_lyrics_sha=lyrics_sha,
        )

    def fetch_and_convert(self, song_path: str) -> None:
        """Entry point - event listener for `song_downloaded`."""
        try:
            self._do_fetch_and_convert(song_path)
        except Exception:
            logger.exception("Unexpected error fetching lyrics for %s", song_path)

    def _do_fetch_and_convert(self, song_path: str) -> None:
        # User-supplied Aegisub files (without the auto-lyrics marker) are sacred.
        if _user_owned_ass(song_path):
            logger.debug("Skipping: user-supplied .ass present for %s", song_path)
            self._register_user_ass(song_path)
            _cleanup_yt_subs_and_info(song_path, self._db)
            return

        info = _read_info_json(song_path)

        # Fetch LRC up front so we can fingerprint it BEFORE deciding whether
        # the cached .ass is still valid. Subtitle changes (LRCLib updated the
        # lyrics for this song) must force a whisper re-run even if the audio
        # and models haven't moved.
        lrc, info = self._fetch_lrc_with_itunes_fallback(info)
        lyrics_sha = _lrc_sha(lrc) if lrc else None

        self._maybe_drop_stale_auto_ass(song_path, lyrics_sha)

        # Cache hit: word-level .ass survived every invalidation trigger
        # (aligner/demucs models + LRC content). Re-requesting a cached song
        # (yt-dlp rewrites info.json on a cache hit) would otherwise overwrite
        # it with line-level and re-run whisper every time.
        if _is_word_level_auto_ass(song_path):
            _cleanup_yt_subs_and_info(song_path, self._db)
            return

        # Decide the source BEFORE writing anything so the .ass is written
        # exactly once per run (US-14). Precedence: LRCLib > YouTube VTT.
        wrote_from_vtt = False
        wrote_from_lrc = False

        if lrc:
            ass = _lrc_to_ass_line_level(lrc)
            if ass:
                _write_ass_atomic(song_path, ass)
                logger.info(
                    "LRCLib: wrote line-level .ass for %s - %s",
                    info["artist"] if info else "?",
                    info["track"] if info else "?",
                )
                self._register_ass(
                    song_path,
                    lyrics_source="lrclib",
                    aligner_model=None,
                    lyrics_sha=lyrics_sha,
                )
                wrote_from_lrc = True

        if not wrote_from_lrc:
            vtt_path = _pick_best_vtt(song_path, preferred_lang=self._db_language(song_path))
            if vtt_path and _try_write_ass_from_vtt_path(song_path, vtt_path):
                logger.info(
                    "Wrote .ass from YouTube VTT for %s",
                    os.path.basename(song_path),
                )
                self._register_ass(
                    song_path,
                    lyrics_source="youtube_vtt",
                    aligner_model=None,
                    lyrics_sha=None,
                )
                self._persist_vtt_language(song_path, vtt_path)
                wrote_from_vtt = True

        _cleanup_yt_subs_and_info(song_path, self._db)

        if not wrote_from_vtt and not wrote_from_lrc:
            logger.info("No lyrics source for %s", os.path.basename(song_path))
            try:
                self._events.emit(
                    "song_warning",
                    {
                        "message": "No lyrics found",
                        "detail": "YouTube captions and LRCLib both had no match.",
                        "song": os.path.basename(song_path),
                        "severity": "warning",
                    },
                )
            except Exception:
                logger.exception("failed to emit song_warning for missing lyrics")
            return

        self._events.emit(
            "notification",
            f"Lyrics ready: {_title_from_filename(song_path)}",
            "info",
        )

        # Per-word forced alignment requires reference lyrics text.
        if self._aligner and lrc:
            # Eagerly kick off Demucs so the aligner gets vocals, not the raw mix.
            _prewarm_stems(song_path)
            Thread(
                target=self._upgrade_to_word_level,
                args=(song_path, lrc, lyrics_sha),
                name=f"lyrics-align-{os.path.basename(song_path)}",
                daemon=True,
            ).start()

    def _db_language(self, song_path: str) -> str | None:
        """Return the DB-stored language for a song (e.g. "en", "pl-PL"), or None."""
        if self._db is None:
            return None
        try:
            song_id = self._db.get_song_id_by_path(song_path)
        except Exception:
            return None
        if song_id is None:
            return None
        row = self._db.get_song_by_id(song_id)
        if row is None:
            return None
        try:
            return row["language"]
        except (KeyError, IndexError):
            return None

    def _persist_vtt_language(self, song_path: str, vtt_path: str) -> None:
        """Write the chosen VTT's lang code to songs.language so subsequent
        runs (and whisperx alignment) skip audio-based language detection.
        US-14 P1.
        """
        if self._db is None:
            return
        lang = _vtt_lang_from_filename(song_path, vtt_path)
        if not lang:
            return
        try:
            song_id = self._db.get_song_id_by_path(song_path)
        except Exception:
            logger.exception("failed to look up song_id to persist VTT language")
            return
        if song_id is None:
            return
        try:
            row = self._db.get_song_by_id(song_id)
            if row is not None and row["language"]:
                return
        except (KeyError, IndexError):
            pass
        try:
            self._db.update_track_metadata_with_provenance(
                song_id, "scanner", {"language": lang}
            )
        except Exception:
            logger.exception("failed to persist VTT language for song_id=%s", song_id)

    def _fetch_lrc_with_itunes_fallback(self, info: dict | None) -> tuple[str | None, dict | None]:
        """Query LRCLib; on miss, canonicalize metadata via iTunes and retry.

        Returns (lrc_or_None, info_with_updated_fields_or_None). ``info`` is
        returned with canonical artist/track when the iTunes fallback hit, so
        later log lines show the cleaned names.
        """
        if not info:
            return None, info
        lrc = _fetch_lrclib(info["track"], info["artist"], info["duration"])
        if lrc:
            return lrc, info
        clean = resolve_metadata(f"{info['artist']} - {info['track']}")
        if not clean:
            return None, info
        logger.info(
            "iTunes canonicalized %r / %r -> %r / %r",
            info["artist"],
            info["track"],
            clean["artist"],
            clean["track"],
        )
        lrc = _fetch_lrclib(clean["track"], clean["artist"], info["duration"])
        if lrc:
            info = {**info, "artist": clean["artist"], "track": clean["track"]}
        return lrc, info

    def _upgrade_to_word_level(self, song_path: str, lrc: str, lyrics_sha: str | None) -> None:
        if self._aligner is None:
            return
        try:
            audio_path = _wait_for_alignment_audio(song_path)
            plain = _lrc_plain_text(lrc)
            # Language fast-path. Order of preference:
            #   1. Cached on the song row (info.json, enricher, prior run, or
            #      manual edit — all authoritative).
            #   2. Detected from the LRC text. Lyrics are clean prose, hundreds
            #      of words; text-detection is far more reliable than whisperx's
            #      audio-based pass and skips its ~20 s startup cost.
            #   3. None — let whisperx detect from audio.
            song_id = self._db.get_song_id_by_path(song_path) if self._db else None
            db_lang = None
            if self._db is not None and song_id is not None:
                row = self._db.get_song_by_id(song_id)
                db_lang = row["language"] if row is not None else None
            language = db_lang or _detect_language(plain)
            words = self._aligner.align(audio_path, plain, language=language)
            final_lang = language or getattr(self._aligner, "last_detected_language", None)
            if self._db is not None and song_id is not None and final_lang and not db_lang:
                # Language detected from LRC text or whisperx audio pass.
                # Source "scanner" keeps it as the lowest confidence so an
                # iTunes/MusicBrainz enrichment can still correct it later.
                self._db.update_track_metadata_with_provenance(
                    song_id, "scanner", {"language": final_lang}
                )
            if not words:
                return
            anim_params = _anim_params_for_bpm(_estimate_bpm(audio_path))
            ass = _words_to_ass_with_k_tags(words, lrc, params=anim_params)
            if ass:
                from pikaraoke.lib.demucs_processor import CACHE_DIR

                _write_ass_atomic(song_path, ass)
                logger.info(
                    "Upgraded to per-word .ass for %s (audio=%s)",
                    song_path,
                    "vocals stem" if audio_path.startswith(CACHE_DIR) else "raw mix",
                )
                aligner_id = self._aligner.model_id if self._aligner else None
                self._register_ass(
                    song_path,
                    lyrics_source="whisperx",
                    aligner_model=aligner_id,
                    lyrics_sha=lyrics_sha,
                )
                self._events.emit(
                    "notification",
                    f"Synced lyrics ready: {_title_from_filename(song_path)}",
                    "success",
                )
                self._events.emit("lyrics_upgraded", song_path)
        except Exception as e:
            logger.warning(
                "word-level alignment failed for %s, keeping line-level",
                song_path,
                exc_info=True,
            )
            try:
                self._events.emit(
                    "song_warning",
                    {
                        "message": "Word-level alignment failed",
                        "detail": f"{type(e).__name__}: {e}",
                        "song": os.path.basename(song_path),
                        "severity": "warning",
                    },
                )
            except Exception:
                logger.exception("failed to emit song_warning for alignment failure")

    def reprocess_library(self, song_paths: list[str]) -> int:
        """Upgrade existing line-level auto-lyrics to word-level in the background.

        Candidates are songs with an auto-generated ``.ass`` (carries the marker)
        that lacks ``\\k`` tags - i.e. files produced before whisperx was
        available. No-op when no aligner is configured or nothing qualifies.

        Returns the number of songs scheduled for upgrade. Processing runs
        serially in a single daemon thread so the aligner doesn't thrash CPU/GPU.
        """
        if self._aligner is None:
            return 0
        candidates = [p for p in song_paths if _needs_word_level_upgrade(p)]
        if not candidates:
            return 0
        logger.info(
            "Reprocessing %d song(s) to word-level karaoke captions in the background",
            len(candidates),
        )
        Thread(
            target=self._reprocess_batch,
            args=(candidates,),
            name="lyrics-reprocess",
            daemon=True,
        ).start()
        return len(candidates)

    def _reprocess_batch(self, song_paths: list[str]) -> None:
        for song_path in song_paths:
            try:
                self._reprocess_one(song_path)
            except Exception:
                logger.exception("reprocess failed for %s", song_path)

    def _reprocess_one(self, song_path: str) -> None:
        """Re-fetch LRCLib from the filename-derived title, then align to word-level."""
        if self._aligner is None:
            return
        if not _needs_word_level_upgrade(song_path):
            return  # raced with another update
        title = _title_from_filename(song_path)
        if not title:
            logger.debug("reprocess: could not extract title from %s", song_path)
            return
        meta = resolve_metadata(title)
        if not meta:
            logger.debug("reprocess: iTunes had no match for %r", title)
            return
        lrc = _fetch_lrclib(meta["track"], meta["artist"], None)
        if not lrc:
            logger.debug(
                "reprocess: LRCLib had no match for %r / %r", meta["artist"], meta["track"]
            )
            return
        _prewarm_stems(song_path)
        self._upgrade_to_word_level(song_path, lrc, _lrc_sha(lrc))


def _needs_word_level_upgrade(song_path: str) -> bool:
    """True when <stem>.ass is auto-generated AND has no \\k tags yet."""
    path = _ass_path(song_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    if ASS_MARKER not in content:
        return False  # user-owned Aegisub file
    # Any \k tag means it's already word-level.
    return "\\k" not in content


def _lrc_sha(lrc: str) -> str:
    """Stable content fingerprint for an LRC payload.

    Used as the cache key for whisper alignment output: same input lyrics ->
    same alignment, so a matching sha lets us keep the existing .ass across
    re-downloads. A changed sha (LRCLib updated the lyrics) invalidates it.
    """
    return hashlib.sha256(lrc.encode("utf-8")).hexdigest()


def _is_word_level_auto_ass(song_path: str) -> bool:
    """True when <stem>.ass is auto-generated AND already word-level (\\k tags present)."""
    path = _ass_path(song_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    return ASS_MARKER in content and "\\k" in content


def _title_from_filename(song_path: str) -> str:
    """Strip the 11-char YouTube ID suffix (both ``---ID`` and ``[ID]`` forms).

    Lightweight replacement for SongManager.filename_from_path so lyrics.py
    stays free of the SongManager dependency.
    """
    stem = os.path.splitext(os.path.basename(song_path))[0]
    # Triple-dash PiKaraoke form
    m = re.search(r"---([A-Za-z0-9_-]{11})$", stem)
    if m:
        return stem[: m.start()].strip()
    # yt-dlp brackets form
    m = re.search(r"\s*\[([A-Za-z0-9_-]{11})\]$", stem)
    if m:
        return stem[: m.start()].strip()
    return stem.strip()


# ----- info.json reading -----


def _info_json_path(song_path: str) -> str:
    stem, _ext = os.path.splitext(song_path)
    return f"{stem}.info.json"


def _ass_path(song_path: str) -> str:
    stem, _ext = os.path.splitext(song_path)
    return f"{stem}.ass"


def _read_info_json(song_path: str) -> dict | None:
    """Read yt-dlp info.json and extract track/artist/duration.

    Falls back to parsing "Artist - Track" from the title when yt-dlp
    didn't provide dedicated fields (common for non-music videos).
    Returns None when artist/track cannot be determined.
    """
    path = _info_json_path(song_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read %s: %s", path, e)
        return None

    track = (data.get("track") or "").strip()
    artist = (data.get("artist") or "").strip()
    duration = data.get("duration")

    if not track or not artist:
        title = (data.get("title") or "").strip()
        if " - " in title:
            left, right = title.split(" - ", 1)
            artist = artist or left.strip()
            track = track or right.strip()

    if not track or not artist:
        return None
    return {"track": track, "artist": artist, "duration": duration}


def _cleanup_info_json(song_path: str) -> None:
    path = _info_json_path(song_path)
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError as e:
            logger.warning("failed to remove %s: %s", path, e)


# ----- LRCLib client -----


def _fetch_lrclib(track: str, artist: str, duration: int | float | None) -> str | None:
    """Query LRCLib for syncedLyrics; None when none found or request failed."""
    get_params: dict[str, str | int] = {"track_name": track, "artist_name": artist}
    if duration:
        get_params["duration"] = int(duration)
    try:
        r = requests.get(f"{LRCLIB_BASE}/api/get", params=get_params, timeout=LRCLIB_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            synced = data.get("syncedLyrics")
            if synced:
                return synced
        r = requests.get(
            f"{LRCLIB_BASE}/api/search",
            params={"track_name": track, "artist_name": artist},
            timeout=LRCLIB_TIMEOUT,
        )
        if r.status_code == 200:
            for item in r.json():
                synced = item.get("syncedLyrics")
                if synced:
                    return synced
    except (requests.RequestException, ValueError) as e:
        logger.warning("LRCLib request failed: %s", e)
    return None


# ----- LRC parser -----


def _parse_lrc(lrc: str) -> list[tuple[float, str]]:
    """Parse LRC into sorted [(start_seconds, text), ...].

    Handles multi-time lines like `[00:12.34][00:25.67]chorus` by duplicating
    the text for each timestamp. Fractional seconds are interpreted as a
    decimal fraction (so `.45` = 0.45s, `.450` = 0.450s).
    """
    entries: list[tuple[float, str]] = []
    for raw in lrc.splitlines():
        tags = _LRC_TAG.findall(raw)
        if not tags:
            continue
        text = _LRC_TAG.sub("", raw).strip()
        if not text:
            continue
        for mm, ss, frac in tags:
            frac_s = int(frac) / (10 ** len(frac)) if frac else 0.0
            start = int(mm) * 60 + int(ss) + frac_s
            entries.append((start, text))
    entries.sort(key=lambda e: e[0])
    return entries


def _lrc_plain_text(lrc: str) -> str:
    """Tags stripped; one line per LRC entry. For forced-alignment reference."""
    return "\n".join(text for _start, text in _parse_lrc(lrc))


_LANGDETECT_MIN_CHARS = 30


def _detect_language(text: str) -> str | None:
    """Best-effort 2-letter language code from text. None on failure.

    Lyrics are an ideal input for text-based detection (hundreds of words of
    clean prose), so a successful classification here lets the aligner skip
    whisperx's slow audio-based detection pass. Returns None when langdetect
    is not installed (optional ``[align]`` extra) or the input is too short
    to classify confidently.
    """
    text = text.strip()
    if len(text) < _LANGDETECT_MIN_CHARS:
        return None
    try:
        import langdetect
    except ImportError:
        return None
    langdetect.DetectorFactory.seed = 0  # deterministic across calls
    try:
        return langdetect.detect(text)
    except langdetect.lang_detect_exception.LangDetectException:
        return None


# First minute is plenty for tempo classification and keeps CPU well under a
# second on the CI/RPi box. librosa itself is heavy to import, so the call
# stays lazy.
_BPM_ANALYSIS_DURATION_S = 60.0


def _estimate_bpm(audio_path: str) -> float | None:
    """Best-effort song tempo in BPM, or None if detection fails.

    Used only to pick decorative animation parameters - never in a timing
    path - so any failure falls through to a plain (un-pulsed) render.
    """
    try:
        import librosa
    except ImportError:
        return None
    try:
        y, sr = librosa.load(audio_path, sr=None, mono=True, duration=_BPM_ANALYSIS_DURATION_S)
        tempo, _beats = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
        logger.info("Estimated BPM %.1f for %s", bpm, audio_path)
        return bpm if bpm > 0 else None
    except Exception:
        logger.warning("BPM estimation failed for %s", audio_path, exc_info=True)
        return None


# ----- ASS builders -----


@dataclass(frozen=True)
class _AnimParams:
    """Per-word decorative animation knobs, driven by song tempo.

    ``pulse_pct`` is the scale peak as a whole-number percent (100 disables
    the pulse entirely). ``pulse_rise_frac`` is the fraction of the word's
    duration spent scaling up; the remainder eases back to 100%.
    """

    pulse_pct: int
    pulse_rise_frac: float


def _anim_params_for_bpm(bpm: float | None) -> _AnimParams:
    """Map tempo to pulse shape. Unknown tempo = no pulse (plain \\kf fill).

    Classification is deliberately coarse: the pulse is decorative, so being
    one tier off is imperceptible. Fast songs get a bigger, snappier pop
    (smaller rise fraction = sharper attack); ballads get a gentler rise.
    """
    if bpm is None or bpm <= 0:
        return _AnimParams(pulse_pct=100, pulse_rise_frac=0.0)
    if bpm < 80:
        return _AnimParams(pulse_pct=103, pulse_rise_frac=0.35)
    if bpm < 130:
        return _AnimParams(pulse_pct=106, pulse_rise_frac=0.25)
    return _AnimParams(pulse_pct=109, pulse_rise_frac=0.15)


# Colors are &HAABBGGRR. PrimaryColour = unsung (bright white — what you read
# next); SecondaryColour = the \kf wipe target for sung words (mid-grey — the
# "already sang it" fade). Outline/shadow softened vs. the old spec so the
# glyphs feel less chromed.
_ASS_STYLE = (
    "Style: Default,Arial,64,&H00FFFFFF,&H00AAAAAA,&H00000000,&HB0000000,"
    "0,0,0,0,100,100,0,0,1,2,1,2,40,40,80,1"
)


def _ass_header() -> str:
    return (
        "[Script Info]\n"
        f"Title: {ASS_MARKER}\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{_ASS_STYLE}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _format_ass_time(seconds: float) -> str:
    """ASS uses H:MM:SS.cc (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


_LAST_LINE_HOLD_S = 5.0

# Multi-line context window: show up to 2 past lines + current + up to 2 future
# lines per Dialogue, with the future cap limited to 5s so a long pause between
# verses doesn't leak spoilers onto the screen.
_CONTEXT_BEFORE = 2
_CONTEXT_AFTER = 2
_CONTEXT_FORWARD_WINDOW_S = 5.0


def _context_window_texts(entries: list[tuple[float, str]], i: int) -> tuple[list[str], list[str]]:
    """Pick the past / future lines visible alongside ``entries[i]``."""
    past = [text for _t, text in entries[max(0, i - _CONTEXT_BEFORE) : i]]
    start_t = entries[i][0]
    future: list[str] = []
    for j in range(i + 1, min(i + 1 + _CONTEXT_AFTER, len(entries))):
        t_j, text_j = entries[j]
        if t_j - start_t > _CONTEXT_FORWARD_WINDOW_S:
            break
        future.append(text_j)
    return past, future


def _render_context_block(past_ass: list[str], current_ass: str, future_ass: list[str]) -> str:
    """Compose the centered multi-line Dialogue body.

    Current line is opaque + bold; past/future are dimmed (alpha 0x80). Middle-
    center alignment (``\\an5``) stacks the block vertically on-screen. Callers
    pass already-escaped / ``\\k``-tagged strings so this helper never re-escapes.
    """
    dim = r"{\alpha&H80&\b0}"
    hot = r"{\alpha&H00&\b1}"
    segments = [f"{dim}{t}" for t in past_ass]
    segments.append(f"{hot}{current_ass}")
    segments.extend(f"{dim}{t}" for t in future_ass)
    return r"{\an5}" + r"\N".join(segments)


def _lrc_to_ass_line_level(lrc: str) -> str | None:
    """Convert LRC to ASS with a centered 5-line context window per entry."""
    entries = _parse_lrc(lrc)
    if not entries:
        return None
    out = [_ass_header()]
    for i, (start, text) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else start + _LAST_LINE_HOLD_S
        past_raw, future_raw = _context_window_texts(entries, i)
        body = _render_context_block(
            [_escape_ass(t) for t in past_raw],
            _escape_ass(text),
            [_escape_ass(t) for t in future_raw],
        )
        out.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{body}\n"
        )
    return "".join(out)


# Accept words whose timing drifts up to this far outside the LRC line window
# before we distrust the alignment and fall back to static text.
_ALIGNMENT_TOLERANCE_S = 2.0


def _words_to_ass_with_k_tags(
    words: list[Word], lrc: str, params: _AnimParams | None = None
) -> str | None:
    """Rebuild ASS with \\kf karaoke tags on the current line, plain text on context lines.

    Aligner output is 1:1 with reference-text tokens (see
    ``map_whisper_to_reference``), so we assign words to LRC entries by
    position - not by timestamp. Time-based matching collapses badly when
    whisper mis-times a region of the song: hundreds of later-line words end
    up stuffed into a single LRC entry's time window. Lines whose aligned
    times don't overlap the LRC window fall back to static text.

    ``params`` controls the decorative per-word pulse; when ``None`` the
    words render as plain \\kf fills with no scaling effect.
    """
    entries = _parse_lrc(lrc)
    if not entries:
        return None
    out = [_ass_header()]
    word_idx = 0
    for i, (start, text) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else start + _LAST_LINE_HOLD_S
        expected = len(text.split())
        line_words = words[word_idx : word_idx + expected]
        word_idx += expected
        if line_words and _words_overlap_window(line_words, start, end):
            current_ass = " ".join(_k_token(w, start, params) for w in line_words)
        else:
            current_ass = _escape_ass(text)
        past_raw, future_raw = _context_window_texts(entries, i)
        body = _render_context_block(
            [_escape_ass(t) for t in past_raw],
            current_ass,
            [_escape_ass(t) for t in future_raw],
        )
        out.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{body}\n"
        )
    return "".join(out)


def _words_overlap_window(words: list[Word], start: float, end: float) -> bool:
    """True when the aligned words' span overlaps the LRC line window."""
    first = words[0].start
    last = words[-1].end
    return last >= start - _ALIGNMENT_TOLERANCE_S and first <= end + _ALIGNMENT_TOLERANCE_S


def _k_token(word: Word, line_start_s: float = 0.0, params: _AnimParams | None = None) -> str:
    """ASS karaoke tag for one word.

    Emits ``\\kf`` (smooth left-to-right color wipe) instead of the older
    ``\\k`` (instant flip) so sung words fade into the secondary colour
    rather than popping. When ``params.pulse_pct`` exceeds 100 we also wrap
    the glyphs in a ``\\t`` scale transform that pulses up and releases
    within the word's own time window - this is the tempo-responsive layer.

    ``\\t`` offsets are measured in milliseconds from the enclosing Dialogue
    event's start, hence the ``line_start_s`` argument.
    """
    dur_cs = max(1, int(round((word.end - word.start) * 100)))
    escaped = _escape_ass(word.text)
    if params is None or params.pulse_pct <= 100:
        return f"{{\\kf{dur_cs}}}{escaped}"
    total_ms = dur_cs * 10
    off_ms = max(0, int(round((word.start - line_start_s) * 1000)))
    rise_ms = max(1, int(total_ms * params.pulse_rise_frac))
    rise_end = off_ms + rise_ms
    fall_end = off_ms + total_ms
    pct = params.pulse_pct
    pulse = (
        f"{{\\t({off_ms},{rise_end},\\fscx{pct}\\fscy{pct})"
        f"\\t({rise_end},{fall_end},\\fscx100\\fscy100)}}"
    )
    return f"{{\\kf{dur_cs}}}{pulse}{escaped}"


def _escape_ass(text: str) -> str:
    # Curly braces delimit override blocks in ASS; escape them.
    return text.replace("{", "\\{").replace("}", "\\}")


# ----- atomic write -----


def _write_ass_atomic(song_path: str, ass_content: str) -> None:
    """Write <stem>.ass atomically so a concurrent read never sees partial data."""
    target = _ass_path(song_path)
    directory = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(suffix=".ass", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ass_content)
        os.replace(tmp, target)
    except OSError:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# ----- VTT conversion -----


def _try_write_ass_from_vtt_path(song_path: str, vtt_path: str) -> bool:
    """Convert a specific VTT file to ASS. Returns True on success."""
    try:
        with open(vtt_path, encoding="utf-8") as f:
            vtt = f.read()
    except OSError as e:
        logger.warning("failed to read %s: %s", vtt_path, e)
        return False
    ass = _vtt_to_ass(vtt)
    if not ass:
        return False
    _write_ass_atomic(song_path, ass)
    return True


def _vtt_lang_from_filename(song_path: str, vtt_path: str) -> str | None:
    """Extract the language code segment from <stem>.<lang>.vtt, or None."""
    stem, _ext = os.path.splitext(song_path)
    basename = os.path.basename(stem)
    name = os.path.basename(vtt_path)
    if not name.startswith(basename + ".") or not name.endswith(".vtt"):
        return None
    return name[len(basename) + 1 : -len(".vtt")] or None


def _lang_base(lang: str | None) -> str:
    """Normalize a language tag to its primary subtag (e.g. `pl-PL` -> `pl`)."""
    if not lang:
        return ""
    return lang.split("-", 1)[0].split("_", 1)[0].lower()


def _pick_best_vtt(song_path: str, preferred_lang: str | None = None) -> str | None:
    """Return the most suitable <stem>*.vtt path, or None if none exist.

    Preference order:
      1. Files whose primary lang subtag matches ``preferred_lang`` (typically
         the track's DB-stored ``language``) — US-14 P1.
      2. Manual uploads (no `-orig` / `-auto` suffix).
      3. Shorter language codes (e.g. `pl` beats `pl-PL`).
      4. Alphabetical as a final tiebreaker.
    """
    stem, _ext = os.path.splitext(song_path)
    directory = os.path.dirname(stem) or "."
    basename = os.path.basename(stem)
    preferred_base = _lang_base(preferred_lang)
    candidates = []
    for name in os.listdir(directory):
        if not name.endswith(".vtt"):
            continue
        if not name.startswith(basename + "."):
            continue
        lang = name[len(basename) + 1 : -len(".vtt")]
        is_auto = "-orig" in lang or lang.endswith("-auto") or "auto" in lang
        # False sorts before True, so "lang matches preferred" goes first.
        lang_matches = bool(preferred_base) and _lang_base(lang) == preferred_base
        candidates.append(
            (not lang_matches, is_auto, len(lang), lang, os.path.join(directory, name))
        )
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][4]


def _parse_vtt_cues(vtt: str) -> list[tuple[float, float, str]]:
    """Parse WEBVTT into [(start_s, end_s, text)]. Inline tags stripped."""
    cues: list[tuple[float, float, str]] = []
    lines = vtt.splitlines()
    i = 0
    while i < len(lines):
        m = _VTT_CUE.search(lines[i])
        if not m:
            i += 1
            continue
        start = _vtt_ts_to_s(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _vtt_ts_to_s(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = _VTT_TAG.sub("", lines[i]).strip()
            if cleaned:
                text_lines.append(cleaned)
            i += 1
        if text_lines:
            cues.append((start, end, " ".join(text_lines)))
    return _dedup_rolling_cues(cues)


def _dedup_rolling_cues(
    cues: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    """YouTube auto-captions repeat each line in a sliding window.

    If cue N's text starts with cue N-1's text, drop cue N-1 and keep only the
    fullest version. This collapses the sliding-window noise back into one line.
    """
    if not cues:
        return cues
    out: list[tuple[float, float, str]] = []
    for cue in cues:
        if out and cue[2].startswith(out[-1][2]):
            # Replace previous with the more complete version.
            out[-1] = (out[-1][0], cue[1], cue[2])
        else:
            out.append(cue)
    return out


def _vtt_ts_to_s(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def _vtt_to_ass(vtt: str) -> str | None:
    cues = _parse_vtt_cues(vtt)
    if not cues:
        return None
    out = [_ass_header()]
    for start, end, text in cues:
        out.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{_escape_ass(text)}\n"
        )
    return "".join(out)


# ----- ownership check + cleanup -----


def _user_owned_ass(song_path: str) -> bool:
    """True when <stem>.ass exists but was NOT produced by PiKaraoke."""
    path = _ass_path(song_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(512)
    except OSError:
        return True  # safer to assume user-owned if unreadable
    return ASS_MARKER not in head


def _cleanup_yt_subs_and_info(song_path: str, db=None) -> None:
    """Remove yt-dlp byproducts (<stem>*.vtt, <stem>.info.json) after conversion.

    When ``db`` is provided, also unregister the matching ``vtt`` and
    ``info_json`` rows in ``song_artifacts`` so the DB stays in sync with
    disk (US-29 treats the DB as authoritative).
    """
    stem, _ext = os.path.splitext(song_path)
    directory = os.path.dirname(stem) or "."
    basename = os.path.basename(stem)
    try:
        entries = os.listdir(directory)
    except OSError:
        entries = []
    for name in entries:
        if not name.startswith(basename + "."):
            continue
        if name.endswith(".vtt"):
            try:
                os.unlink(os.path.join(directory, name))
            except OSError as e:
                logger.warning("failed to remove %s: %s", name, e)
    _cleanup_info_json(song_path)

    if db is None:
        return
    try:
        song_id = db.get_song_id_by_path(song_path)
    except Exception:
        logger.exception("failed to look up song_id for artifact cleanup: %s", song_path)
        return
    if song_id is None:
        return
    for role in ("vtt", "info_json"):
        try:
            db.delete_artifacts_by_role(song_id, role)
        except Exception:
            logger.exception("failed to unregister %s artifacts for song_id=%s", role, song_id)


# ----- Demucs stem coupling -----
#
# Whisper alignment quality improves materially when fed clean vocals instead
# of the full mix. When whisper is configured, LyricsService triggers a Demucs
# prewarm at download time (see `_prewarm_stems`) and waits briefly for the
# vocals stem to appear before running the aligner.

_STEM_WAIT_TIMEOUT_S = 120.0
_STEM_WAIT_POLL_S = 2.0


def _alignment_audio_path(song_path: str) -> str | None:
    """Return vocals MP3 path when Demucs has finished encoding, else None.

    Cache is keyed by ``resolve_audio_source`` (sibling ``.m4a`` when present),
    matching how ``prewarm`` populates it. Querying with the raw mp4 would miss.

    Only the MP3 tier is returned; the WAV tier is short-lived (removed as
    soon as MP3 encoding finishes) and returning it can cause whisperx to
    open a file that is deleted moments later. Waiting for MP3 is safe:
    whisperx accepts it transparently.
    """
    try:
        from pikaraoke.lib.demucs_processor import (
            get_cache_key,
            get_cached_stems,
            resolve_audio_source,
        )

        cached = get_cached_stems(get_cache_key(resolve_audio_source(song_path)))
    except Exception as e:
        logger.warning("stem lookup failed for %s: %s", song_path, e)
        return None
    if not cached:
        return None
    vocals_path, _instr_path, fmt = cached
    if fmt != "mp3":
        return None
    return vocals_path


def _wait_for_alignment_audio(song_path: str) -> str:
    """Poll for a cached vocals stem up to `_STEM_WAIT_TIMEOUT_S`, else fall back.

    Fallback is the audio-only sibling (``resolve_audio_source``) so we don't
    feed whisperx a video-only mp4 from the split-streams download flow.
    """
    stem = _alignment_audio_path(song_path)
    if stem is not None:
        return stem
    deadline = time.monotonic() + _STEM_WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(_STEM_WAIT_POLL_S)
        stem = _alignment_audio_path(song_path)
        if stem is not None:
            return stem
    logger.info(
        "stems not ready within %.0fs for %s; aligning on raw mix",
        _STEM_WAIT_TIMEOUT_S,
        os.path.basename(song_path),
    )
    from pikaraoke.lib.demucs_processor import resolve_audio_source

    return resolve_audio_source(song_path)


def _prewarm_stems(song_path: str) -> None:
    """Fire-and-forget Demucs prewarm so alignment has vocals ready."""
    try:
        from pikaraoke.lib.demucs_processor import prewarm

        prewarm(song_path)
    except Exception as e:
        logger.warning("Demucs prewarm failed for %s: %s", song_path, e)
