"""Auto-fetch synced lyrics from LRCLib and render as ASS subtitles.

Pipeline:
  song_downloaded event -> LyricsService.fetch_and_convert
    1. Read track/artist/duration from the ``songs`` table
       (``register_download`` seeded them from yt-dlp's info.json).
    2. Query LRCLib for syncedLyrics.
    3. Convert LRC to line-level ASS and write <stem>.ass.
    4. (Optional) in a background thread, run forced alignment and
       replace the ASS with per-word \\k-tagged highlighting.

The existing .ass stack (FileResolver, SubtitlesOctopus in splash.js)
renders the output automatically - no UI changes required.

When LRCLib and VTT conversion both fail, the original ``<stem>*.vtt``
is left on disk so the user's YouTube captions are not deleted along
with the failed conversion attempt — raw captions beat zero captions.
"""

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from threading import Thread
from typing import Protocol

import librosa
import requests

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.lyrics_audio_probe import probe_language as _probe_audio_language
from pikaraoke.lib.lyrics_audio_probe import (
    probe_language_whole_song as _probe_audio_language_whole_song,
)
from pikaraoke.lib.lyrics_language_classifier import (
    classify_and_persist as _classify_language,
)
from pikaraoke.lib.lyrics_language_classifier import read_info_json as _read_info_json
from pikaraoke.lib.music_metadata import (
    _itunes_row_to_dict,
    _normalize_title,
    _search_itunes_cached,
    fetch_musicbrainz_language_signals,
    resolve_metadata,
)

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net"
LRCLIB_TIMEOUT = 5.0

GENIUS_BASE = "https://api.genius.com"
GENIUS_TIMEOUT = 5.0
GENIUS_ACCESS_TOKEN = os.environ.get("GENIUS_ACCESS_TOKEN", "").strip()

# Last-resort ASR fallback. When LRCLib / Genius / YouTube VTT all miss,
# transcribe the vocals stem with faster-whisper so the song still gets
# subtitles (flagged as auto-generated in the UI). Set the env var to
# one of {"off","none","false","0"} to disable; otherwise the value is
# the faster-whisper model name ("tiny" / "base" / "small" / "medium" /
# "large-v2" / "large-v3" / "large-v3-turbo" / ...).
#
# Default "large-v3-turbo" (aka "turbo"): ~1.5 GB, distilled from
# large-v3 — near-large-v3 transcription quality at ~7x the decoding
# speed. Heavy enough that Demucs-isolated vocals + non-English lyrics
# actually come out legible (small/medium routinely mangle Polish rap)
# but still fits in RAM and runs in ~real-time on a modern CPU with
# int8. Downgrade to "medium" on low-RAM boxes; bump to "large-v3" when
# raw accuracy matters more than wall time.
_WHISPER_OPT_OUT = {"off", "none", "false", "0"}
WHISPER_FALLBACK_MODEL = os.environ.get("WHISPER_FALLBACK_MODEL", "").strip() or "large-v3-turbo"
_whisper_model_cache: list = [None]
_whisper_model_lock = threading.Lock()

# Trailing mix/version markers in parens or brackets: "(Instrumental)",
# "[Karaoke]", "(Acoustic Version)", etc. LRCLib + Genius index lyrics once
# per song regardless of release variant, so these suffixes drop otherwise-
# good matches. Applied to the upstream query only; DB titles are untouched.
_VARIANT_RE = re.compile(
    r"\s*[\(\[]"
    r"[^)\]]*?"
    r"\b(?:instrumental|karaoke|acoustic(?:\s+version)?|live|remix|"
    r"remastered|extended|radio\s+edit)\b"
    r"[^)\]]*"
    r"[\)\]]\s*$",
    re.IGNORECASE,
)

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
class WordPart:
    """Sub-word chunk with its audio-aligned start/end in seconds.

    Used for both per-character alignment (WhisperX path, real wav2vec2
    CTC timings per glyph) and per-syllable alignment (Whisper-ASR
    fallback, pyphen-derived boundaries with uniformly interpolated
    timings inside the word duration). The ASS renderer emits one
    ``\\kf`` tag per part.
    """

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Word:
    """A single word with its start/end time in seconds.

    ``parts`` is the sub-word breakdown used by the ASS renderer to emit
    multiple ``\\kf`` tags inside a single word. On the WhisperX path
    these are per-character with real wav2vec2 CTC timings; on the
    Whisper-ASR fallback path they are per-syllable (pyphen) with
    timings interpolated across the word duration. ``None`` means the
    info is unavailable or the word is a single part - the renderer
    emits one ``\\kf`` spanning the whole word in that case.
    """

    text: str
    start: float
    end: float
    parts: tuple[WordPart, ...] | None = None


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

    @property
    def has_aligner(self) -> bool:
        """True when whisperx (or any word-level aligner) is configured."""
        return self._aligner is not None

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

        Emits ``lyrics_upgraded`` so the splash UI can refresh its
        lyrics_source badge (and cache-bust the subtitle URL if the song
        is already playing and the .ass was swapped in mid-song).
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
        try:
            self._events.emit("lyrics_upgraded", song_path)
        except Exception:
            logger.exception("failed to emit lyrics_upgraded for %s", song_path)

    def _register_user_ass(self, song_path: str) -> None:
        if self._db is None:
            return
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        self._db.upsert_artifacts(song_id, [{"role": "ass_user", "path": _ass_path(song_path)}])
        # Tag the row so the UI badge distinguishes user-authored subtitles
        # from auto-generated ones.
        try:
            self._db.update_processing_config(
                song_id, lyrics_source="user_ass", aligner_model=None, lyrics_sha=None
            )
        except Exception:
            logger.exception("failed to stamp user_ass lyrics_source for %s", song_path)
        try:
            self._events.emit("lyrics_upgraded", song_path)
        except Exception:
            logger.exception("failed to emit lyrics_upgraded for %s", song_path)

    def _emit_stage_notification(self, song_path: str, stage: str) -> None:
        """Toast a pipeline-stage message (e.g. "Fetching lyrics: Song Title").

        Swallows emit exceptions so a missing/misconfigured event bus never
        breaks the stage it was meant to announce.
        """
        if self._events is None:
            return
        try:
            self._events.emit("notification", f"{stage}: {_title_from_filename(song_path)}")
        except Exception:
            logger.exception("failed to emit %s stage notification", stage)

    def _maybe_drop_stale_auto_ass(self, song_path: str, lyrics_sha: str | None) -> None:
        """Delete the auto .ass when any upstream dependency changed.

        Invalidates on: audio sha change (US-15 — source bytes replaced),
        aligner model swap, demucs model swap (whisper aligned to stems from
        the old model), or LRC content change (LRCLib updated the lyrics).
        Runs before re-generating lyrics so stale artifacts are not served.
        """
        if self._db is None:
            return
        song_id = self._db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        from pikaraoke.lib.audio_fingerprint import (
            ensure_audio_fingerprint,
            ensure_lyrics_config,
        )
        from pikaraoke.lib.demucs_processor import DEMUCS_MODEL, resolve_audio_source

        # Audio sha check first — a re-downloaded source invalidates
        # everything downstream (stems + auto .ass via _invalidate_auto_ass).
        # Cheap when mtime+size match the DB.
        try:
            ensure_audio_fingerprint(self._db, song_id, resolve_audio_source(song_path))
        except Exception:
            logger.exception("ensure_audio_fingerprint failed for %s", song_path)

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
        basename = os.path.basename(song_path)
        logger.info("lyrics pipeline: starting for %s", basename)
        # User-supplied Aegisub files (without the auto-lyrics marker) are sacred.
        if _user_owned_ass(song_path):
            logger.info(
                "lyrics pipeline: %s -> user-supplied .ass (skipping auto pipeline)",
                basename,
            )
            self._register_user_ass(song_path)
            _cleanup_yt_vtt(song_path, self._db)
            return

        info = self._read_metadata_for_lrclib(song_path)
        if info:
            logger.info(
                "lyrics pipeline: %s metadata track=%r artist=%r duration=%s",
                basename,
                info.get("track"),
                info.get("artist"),
                info.get("duration"),
            )
        else:
            logger.info(
                "lyrics pipeline: %s has no usable artist/title — LRCLib query will be skipped",
                basename,
            )

        # Tier 1 classifier (US-43): seed songs.language from every text
        # signal we already have in hand (yt-dlp info.json, cached iTunes
        # hit, cached MusicBrainz recording, langdetect on DB fields). Each
        # signal persists under its own rung in the provenance ladder, so a
        # stronger later source overwrites a weaker one and LRCLib's
        # ``lrc_heuristic`` (lowest rung) can never overwrite anything the
        # classifier seeded. Runs BEFORE the LRC fetch so
        # ``_is_lrc_language_mismatch`` has DB-side ground truth to compare
        # against on cold-DB first runs (the Kolorowy wiatr poison path).
        self._run_language_classifier(song_path, info)

        # Tell the operator we're about to hit LRCLib / iTunes. Emitted
        # BEFORE the network call so the "Fetching lyrics…" toast lands
        # while the HTTP round-trip is in flight. Skipped above if a
        # user-supplied .ass preempts the whole pipeline.
        self._emit_stage_notification(song_path, "Fetching lyrics")

        # Fetch LRC up front so we can fingerprint it BEFORE deciding whether
        # the cached .ass is still valid. Subtitle changes (LRCLib updated the
        # lyrics for this song) must force a whisper re-run even if the audio
        # and models haven't moved.
        lrc, info = self._fetch_lrc_with_itunes_fallback(info)
        if lrc and self._is_lrc_language_mismatch(song_path, lrc):
            # Dub-trap: LRCLib indexes by canonical song name, so a Polish
            # dub of an English original gets the English lyrics (the
            # Pocahontas "Kolorowy wiatr" case). When the DB already knows
            # the audio language, reject the LRC and fall through to the
            # other sources — Whisper ASR on the vocals stem produces
            # matching-language subs, VTT might carry the dub captions.
            lrc = None
        lyrics_sha = _lrc_sha(lrc) if lrc else None

        self._maybe_drop_stale_auto_ass(song_path, lyrics_sha)

        # Cache hit: word-level .ass survived every invalidation trigger
        # (aligner/demucs models + LRC content). Re-requesting a cached song
        # (yt-dlp rewrites info.json on a cache hit) would otherwise overwrite
        # it with line-level and re-run whisper every time.
        if _is_word_level_auto_ass(song_path):
            logger.info(
                "lyrics pipeline: %s -> word-level .ass cache hit (no work)",
                basename,
            )
            _cleanup_yt_vtt(song_path, self._db)
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

        wrote_from_genius = False
        if not wrote_from_lrc and self._aligner is not None and GENIUS_ACCESS_TOKEN and info:
            wrote_from_genius = self._try_genius_fallback(song_path, info)

        if not wrote_from_lrc and not wrote_from_genius:
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

        # VTT cleanup is conditional: only drop YouTube's raw captions once
        # we have our own .ass. When both LRCLib and VTT conversion failed,
        # leave the VTT on disk — raw captions beat no captions, and a
        # future retry (or the user) can still salvage them.
        if wrote_from_lrc or wrote_from_vtt or wrote_from_genius:
            _cleanup_yt_vtt(song_path, self._db)

        if not wrote_from_vtt and not wrote_from_lrc and not wrote_from_genius:
            if _whisper_fallback_enabled():
                logger.info(
                    "lyrics pipeline: %s -> no LRC/Genius/VTT source; queuing Whisper ASR fallback",
                    basename,
                )
                # Fire-and-forget: ASR is slow (~1x realtime on CPU) and
                # must not block the download pipeline. If it succeeds the
                # .ass lands mid-song and `lyrics_upgraded` flips the UI;
                # if it fails we surface a song_warning from inside.
                Thread(
                    target=self._try_whisper_fallback,
                    args=(song_path,),
                    name=f"whisper-fallback-{os.path.basename(song_path)}",
                    daemon=True,
                ).start()
            else:
                logger.info(
                    "lyrics pipeline: %s -> no LRC/Genius/VTT source; Whisper fallback disabled",
                    basename,
                )
                try:
                    self._events.emit(
                        "song_warning",
                        {
                            "message": "No lyrics found",
                            "detail": "LRCLib / Genius / YouTube captions all missed, and Whisper fallback is disabled.",
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

        source = "lrclib" if wrote_from_lrc else "genius" if wrote_from_genius else "youtube_vtt"
        will_align = bool(self._aligner and lrc)
        logger.info(
            "lyrics pipeline: %s -> source=%s db_lang=%s word_alignment=%s",
            basename,
            source,
            self._db_language(song_path),
            "queued" if will_align else "skipped",
        )

        # Per-word forced alignment requires reference lyrics text.
        if will_align:
            # Eagerly kick off Demucs so the aligner gets vocals, not the raw mix.
            _prewarm_stems(song_path)
            Thread(
                target=self._upgrade_to_word_level,
                args=(song_path, lrc, lyrics_sha),
                name=f"lyrics-align-{os.path.basename(song_path)}",
                daemon=True,
            ).start()

    def _read_metadata_for_lrclib(self, song_path: str) -> dict | None:
        """Return ``{"track", "artist", "duration"}`` from the songs table.

        The DB is authoritative for lyrics: ``register_download`` seeds
        artist/title from yt-dlp's info.json immediately after download,
        enrichment (iTunes/MusicBrainz) may later refine them in-place, and
        scanner-discovered songs get the same backfill. Either raw or
        enriched values feed LRCLib here; if the first query misses,
        ``_fetch_lrc_with_itunes_fallback`` re-canonicalises via iTunes.

        Returns None when artist or title is empty — ``_fetch_lrclib`` has
        no useful query without both, so we skip straight to the "no
        lyrics source" warning upstream.
        """
        if self._db is None:
            return None
        try:
            song_id = self._db.get_song_id_by_path(song_path)
        except Exception:
            logger.exception("failed to look up song_id for %s", song_path)
            return None
        if song_id is None:
            return None
        row = self._db.get_song_by_id(song_id)
        if row is None:
            return None
        track = (row["title"] or "").strip()
        artist = (row["artist"] or "").strip()
        if not track or not artist:
            return None
        return {"track": track, "artist": artist, "duration": row["duration_seconds"]}

    def _run_language_classifier(self, song_path: str, info: dict | None) -> None:
        """Collect Tier 1 language signals and persist each at its rung.

        Every signal source is data we already fetched: yt-dlp's info.json
        (if still on disk — register_download usually consumes it, but a
        scanner-registered song may still have it), the
        ``_search_itunes_cached`` LRU populated by the enricher, and the
        ``_search_musicbrainz_cached`` LRU from the same enrichment pass.
        Cold caches return ``None`` and the extractor silently skips;
        we never fire a fresh HTTP request from this path.

        The classifier writes independently for each signal; the per-rung
        ladder in ``METADATA_SOURCE_CONFIDENCE`` handles winner selection.
        """
        if self._db is None:
            return
        try:
            song_id = self._db.get_song_id_by_path(song_path)
        except Exception:
            logger.exception("classifier: song_id lookup failed for %s", song_path)
            return
        if song_id is None:
            return

        yt_info = _read_info_json(song_path)
        itunes_hit: dict | None = None
        mb_signals: dict | None = None
        if info and info.get("artist") and info.get("track"):
            # Match the enricher's query shape (`_query_from_song` + iTunes'
            # internal ``_normalize_title``) so both paths share the same LRU
            # entry and pay at most one iTunes round-trip per song.
            query = _normalize_title(f"{info['artist']} - {info['track']}")
            try:
                rows = _search_itunes_cached(query, 1)
                if rows:
                    itunes_hit = _itunes_row_to_dict(rows[0])
            except Exception:
                logger.exception("classifier: iTunes lookup failed for %s", song_path)
            try:
                mb_signals = fetch_musicbrainz_language_signals(info["artist"], info["track"])
            except Exception:
                logger.exception("classifier: MusicBrainz lookup failed for %s", song_path)

        try:
            _signals, verdict = _classify_language(
                self._db,
                song_id,
                song_path=song_path,
                yt_info=yt_info,
                itunes_hit=itunes_hit,
                mb_signals=mb_signals,
                db_title=(info or {}).get("track"),
                db_artist=(info or {}).get("artist"),
            )
        except Exception:
            logger.exception("classifier: classify_and_persist crashed for %s", song_path)
            return

        # Tier 2a (US-43): when Tier 1 couldn't reach consensus, run a
        # Whisper language-ID probe on the raw audio. The probe writes
        # under ``whisper_probe_raw`` (rung 22), which beats every Tier 1
        # signal — text consensus abstained, so acoustic ground truth
        # takes over. No-op when consensus already landed, keeping the
        # happy path at ~50ms.
        if verdict is None:
            self._run_tier2a_probe(song_path, song_id)

    def _run_tier2a_probe(self, song_path: str, song_id: int) -> None:
        """Tier 2a Whisper language-ID probe on raw audio (US-43).

        Runs synchronously on the download-worker thread. Budget is 1-5s
        warm / 5-15s cold; the LRC fetch behind it is a 5s HTTP call, so
        the thread-handoff overhead to parallelise the two would cost
        more than the probe itself on a warm model. Inline is fine.

        Only fires when the Tier 1 text-consensus classifier returned no
        verdict (caller responsibility). Writes under ``whisper_probe_raw``
        (rung 22), which beats every Tier 1 text rung but sits below the
        stems-based ``whisper_probe_stems`` that Tier 2b will write later.
        """
        if self._db is None or not _whisper_fallback_enabled():
            return
        from pikaraoke.lib.audio_fingerprint import ensure_audio_fingerprint
        from pikaraoke.lib.demucs_processor import resolve_audio_source

        audio_path = resolve_audio_source(song_path)
        if not os.path.exists(audio_path):
            return
        try:
            audio_sha = ensure_audio_fingerprint(self._db, song_id, audio_path)
        except Exception:
            logger.exception("tier2a probe: fingerprint failed for %s", song_path)
            return
        if not audio_sha:
            return
        row = self._db.get_song_by_id(song_id)
        try:
            duration = row["duration_seconds"] if row is not None else None
        except (KeyError, IndexError):
            duration = None

        logger.info(
            "US-43 tier2a: %s starting sha=%s duration=%s",
            os.path.basename(song_path),
            audio_sha[:12],
            duration,
        )
        try:
            lang = _probe_audio_language(
                audio_path=audio_path,
                audio_sha256=audio_sha,
                duration_seconds=duration,
                get_model=_get_whisper_model,
                cache_get=self._db.get_metadata,
                cache_set=self._db.set_metadata,
            )
        except Exception:
            logger.exception("tier2a probe: probe_language crashed for %s", song_path)
            return
        if not lang:
            return
        try:
            applied = self._db.update_track_metadata_with_provenance(
                song_id, "whisper_probe_raw", {"language": lang}
            )
        except Exception:
            logger.exception("tier2a probe: failed to persist lang=%s for %s", lang, song_path)
            return
        logger.info(
            "US-43 tier2a: %s lang=%s applied=%s provenance=whisper_probe_raw",
            os.path.basename(song_path),
            lang,
            bool(applied),
        )

    def _run_tier2b_probe(self, song_path: str, song_id: int, stem_path: str) -> bool:
        """Tier 2b Whisper language-ID re-probe on the vocals stem (US-43).

        Returns ``True`` when the probe *flipped* the DB language (caller
        should abort the current alignment pass — the ``.ass`` +
        ``lyrics_sha`` have been invalidated so the next pipeline run
        re-fetches LRC in the corrected language, and the wav2vec2 model
        currently loaded is for the wrong language anyway).

        Returns ``False`` when the probe agrees with the current DB
        language (provenance is bumped to ``whisper_probe_stems``,
        language value unchanged), when the probe is inconclusive, when
        the ladder blocks the write (e.g. a ``manual`` language is
        sticky), or when Whisper isn't available at all — callers treat
        False as "proceed with alignment as normal".

        Unlike Tier 2a, this probe runs the whole song through
        ``detect_language`` with VAD filtering. The vocals stem is
        already clean (instruments gone, silences shortened), so
        averaging language probabilities across every sung segment gives
        meaningfully higher confidence than a single 30s window.
        """
        if self._db is None or not _whisper_fallback_enabled():
            return False

        row = self._db.get_song_by_id(song_id)
        if row is None:
            return False
        audio_sha = row["audio_sha256"]
        if not audio_sha:
            return False
        current_lang = row["language"]

        logger.info(
            "US-43 tier2b: %s starting sha=%s stem=%s current_db_lang=%s",
            os.path.basename(song_path),
            audio_sha[:12],
            os.path.basename(stem_path),
            current_lang,
        )
        try:
            stem_lang = _probe_audio_language_whole_song(
                audio_path=stem_path,
                audio_sha256=audio_sha,
                get_model=_get_whisper_model,
                cache_get=self._db.get_metadata,
                cache_set=self._db.set_metadata,
            )
        except Exception:
            logger.exception("tier2b probe: probe_language_whole_song crashed for %s", song_path)
            return False

        if not stem_lang:
            return False

        try:
            applied = self._db.update_track_metadata_with_provenance(
                song_id, "whisper_probe_stems", {"language": stem_lang}
            )
        except Exception:
            logger.exception("tier2b probe: failed to persist lang=%s for %s", stem_lang, song_path)
            return False

        same_lang = _lang_base(current_lang or "") == stem_lang
        if same_lang:
            logger.info(
                "US-43 tier2b: %s agrees lang=%s applied=%s (provenance -> whisper_probe_stems)",
                os.path.basename(song_path),
                stem_lang,
                bool(applied),
            )
            return False

        if not applied:
            # Current language comes from a higher rung than
            # whisper_probe_stems — practically only ``manual``. Respect it.
            logger.info(
                "US-43 tier2b: %s disagrees (stems=%s, db=%s) but ladder blocked "
                "the write; keeping db value",
                os.path.basename(song_path),
                stem_lang,
                current_lang,
            )
            return False

        # Disagreement, and the write landed. Invalidate the auto ``.ass``
        # + ``lyrics_sha`` + ``aligner_model`` so the next
        # ``_do_fetch_and_convert`` treats the LRC cache as stale and
        # re-fetches in the corrected language. The currently-rendering
        # session keeps whatever line-level ``.ass`` already landed —
        # US-43's "write fast, fix later" path.
        logger.info(
            "US-43 tier2b: %s FLIP stems=%s db=%s; invalidating .ass for re-fetch",
            os.path.basename(song_path),
            stem_lang,
            current_lang,
        )
        try:
            from pikaraoke.lib.audio_fingerprint import _invalidate_auto_ass

            _invalidate_auto_ass(self._db, song_id)
        except Exception:
            logger.exception("tier2b probe: failed to invalidate .ass for %s", song_path)

        # Re-dispatch the pipeline now. Waiting for the "next"
        # ``song_downloaded`` is a dead-letter promise: that event only
        # fires on the first download, so replays of an existing row
        # would stay caption-less forever after a flip. Running on a
        # daemon thread so the caller (``_upgrade_to_word_level``) can
        # still unwind cleanly. The second pass sees the flipped DB
        # language, rejects the wrong-language LRC via
        # ``_is_lrc_language_mismatch``, and falls through to Genius /
        # VTT / Whisper ASR. The 2b probe on the second pass hits the
        # per-sha cache and agrees, so no infinite re-dispatch loop.
        Thread(
            target=self.fetch_and_convert,
            args=(song_path,),
            name=f"lyrics-refetch-{os.path.basename(song_path)}",
            daemon=True,
        ).start()
        return True

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

    def _is_lrc_language_mismatch(self, song_path: str, lrc: str) -> bool:
        """True when the DB-stored audio language disagrees with the LRC's.

        Catches the dub trap: LRCLib indexes by canonical song name, so a
        Polish dub of an English original (e.g. Edyta Górniak's "Kolorowy
        wiatr") gets the English "Colors of the Wind" lyrics served back.
        The pipeline would then render English text timed against Polish
        vocals, and sync looks permanently "off".

        Compares primary subtags only (``pl`` vs ``pl-PL`` counts as a
        match). NULL-cached DB language means "no ground truth yet" —
        trust LRC in that case, because we have no better signal without
        adding a Whisper audio probe. Once Whisper writes its detected
        language back to the DB, the next run will enforce consistency.
        """
        db_lang = self._db_language(song_path)
        if not db_lang:
            return False
        lrc_lang = _detect_language(_lrc_plain_text(lrc))
        if not lrc_lang:
            return False
        if _lang_base(db_lang) == _lang_base(lrc_lang):
            return False
        logger.warning(
            "LRCLib language mismatch for %s: DB=%s, LRC=%s — dropping LRC "
            "to avoid mis-synced subs; falling through to VTT / Whisper",
            os.path.basename(song_path),
            db_lang,
            lrc_lang,
        )
        return True

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
            self._db.update_track_metadata_with_provenance(song_id, "scanner", {"language": lang})
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
            # Whisperx alignment waits on stems and then runs per-word forced
            # alignment; easily the longest stage after Demucs. Surface it so
            # the splash shows progress beyond "Lyrics ready".
            self._emit_stage_notification(song_path, "Aligning words")
            audio_path = _wait_for_alignment_audio(song_path)
            # Stems weren't ready within the 120s budget; whisperx falls back
            # to the raw mix. Surface a song_warning so the operator can
            # correlate poor word-timing with the degraded source. US-9 P2.
            from pikaraoke.lib.demucs_processor import CACHE_DIR as _CACHE_DIR

            if not audio_path.startswith(_CACHE_DIR):
                try:
                    self._events.emit(
                        "song_warning",
                        {
                            "message": "Aligned on raw mix",
                            "detail": (
                                "Stems were not ready within "
                                f"{int(_STEM_WAIT_TIMEOUT_S)}s; word-level timing "
                                "may be less accurate than when vocals are isolated."
                            ),
                            "song": os.path.basename(song_path),
                            "severity": "warning",
                        },
                    )
                except Exception:
                    logger.exception("failed to emit raw-mix fallback song_warning")
            plain = _lrc_plain_text(lrc)
            # Language is required for wav2vec2 forced alignment (models are
            # per-language; the aligner no longer runs whisper ASR so it can't
            # detect from audio). Order of preference:
            #   1. Cached on the song row (info.json, enricher, prior run, or
            #      manual edit — all authoritative).
            #   2. Detected from the LRC text. Lyrics are clean prose, hundreds
            #      of words; text-detection is reliable and essentially free.
            song_id = self._db.get_song_id_by_path(song_path) if self._db else None

            # Tier 2b (US-43): re-validate language on the isolated vocals
            # stem. Fires only when we actually got stems (not the raw-mix
            # timeout fallback at line 747) — probing on the raw mix here
            # would just duplicate Tier 2a on a noisier input. If 2b flips
            # the DB language, abort this alignment pass: wav2vec2 is
            # per-language, aligning with the wrong model is wasted work.
            # ``_run_tier2b_probe`` both invalidates the stale .ass and
            # re-dispatches the pipeline so the corrected-language LRC
            # gets fetched immediately (waiting for a future
            # ``song_downloaded`` would leave replays caption-less).
            if song_id is not None and audio_path.startswith(_CACHE_DIR):
                if self._run_tier2b_probe(song_path, song_id, audio_path):
                    return

            db_lang = None
            if self._db is not None and song_id is not None:
                row = self._db.get_song_by_id(song_id)
                db_lang = row["language"] if row is not None else None
            language = db_lang or _detect_language(plain)
            if not language:
                logger.info(
                    "Skipping word-level alignment for %s: language unknown "
                    "(LRC too short or langdetect missing)",
                    song_path,
                )
                return
            words = self._aligner.align(
                audio_path, plain, lrc_lines=lrc_line_windows(lrc), language=language
            )
            if self._db is not None and song_id is not None and not db_lang:
                # Persist the text-detected language so future runs and UI
                # lookups skip the detection step. ``lrc_heuristic`` sits
                # below every other rung: LRCLib records are occasionally
                # mislabelled (see US-43 Kolorowy wiatr), so any later
                # classifier/enricher signal must be able to overwrite it.
                self._db.update_track_metadata_with_provenance(
                    song_id, "lrc_heuristic", {"language": language}
                )
            if not words:
                return
            bpm = _estimate_bpm(audio_path)
            anim_params = _anim_params_for_bpm(bpm)
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

    def _try_genius_fallback(self, song_path: str, info: dict) -> bool:
        """Fetch plain lyrics from Genius, align them, and write a word-level .ass.

        Runs synchronously inside the ``fetch_and_convert`` worker thread
        (caller already runs there). Returns True on success so the caller
        can skip VTT fallback and the "no lyrics found" warning; False on
        any miss (no Genius match, no stems, no language, aligner failure)
        and the caller falls through to VTT.

        Genius lyrics are plain text — no timestamps — so we align the
        whole song in one pass (``lrc_lines=None``) then synthesise an LRC
        from the aligned word times and reuse the existing word-level ASS
        builder.
        """
        if self._aligner is None:
            return False
        track = info.get("track")
        artist = info.get("artist")
        if not track or not artist:
            return False
        logger.info("Genius: querying track=%r artist=%r", track, artist)
        try:
            genius_text = _fetch_genius(track, artist)
        except Exception:
            logger.exception("Genius fetch crashed for %r / %r", artist, track)
            return False
        if not genius_text:
            logger.info("Genius: miss track=%r artist=%r", track, artist)
            return False
        logger.info(
            "Genius: hit track=%r artist=%r (%d chars)",
            track,
            artist,
            len(genius_text),
        )

        self._emit_stage_notification(song_path, "Aligning Genius lyrics")
        _prewarm_stems(song_path)
        audio_path = _wait_for_alignment_audio(song_path)

        lines = [ln for ln in genius_text.splitlines() if ln.strip()]
        plain = "\n".join(lines)

        song_id = self._db.get_song_id_by_path(song_path) if self._db else None
        db_lang = None
        if self._db is not None and song_id is not None:
            row = self._db.get_song_by_id(song_id)
            db_lang = row["language"] if row is not None else None
        language = db_lang or _detect_language(plain)
        if not language:
            logger.info("Skipping Genius alignment for %s: language unknown", song_path)
            return False

        try:
            words = self._aligner.align(audio_path, plain, language=language)
        except Exception as e:
            logger.warning("Genius alignment failed for %s", song_path, exc_info=True)
            try:
                self._events.emit(
                    "song_warning",
                    {
                        "message": "Genius alignment failed",
                        "detail": f"{type(e).__name__}: {e}",
                        "song": os.path.basename(song_path),
                        "severity": "warning",
                    },
                )
            except Exception:
                logger.exception("failed to emit song_warning for Genius alignment failure")
            return False
        if not words:
            return False

        synthetic_lrc = _lrc_from_aligned_lines(words, lines)
        if not synthetic_lrc:
            return False

        bpm = _estimate_bpm(audio_path)
        ass = _words_to_ass_with_k_tags(words, synthetic_lrc, params=_anim_params_for_bpm(bpm))
        if not ass:
            return False

        _write_ass_atomic(song_path, ass)
        aligner_id = self._aligner.model_id if self._aligner else None
        lyrics_sha = _lrc_sha(synthetic_lrc)
        self._register_ass(
            song_path,
            lyrics_source="genius",
            aligner_model=aligner_id,
            lyrics_sha=lyrics_sha,
        )
        if self._db is not None and song_id is not None and not db_lang:
            # Genius plain lyrics are text-only; same upstream-mislabel risk
            # as LRCLib, so this shares the ``lrc_heuristic`` rung.
            self._db.update_track_metadata_with_provenance(
                song_id, "lrc_heuristic", {"language": language}
            )
        logger.info("Genius: wrote word-level .ass for %s - %s", artist, track)
        return True

    def _try_whisper_fallback(self, song_path: str) -> None:
        """Last-resort ASR: transcribe the vocals stem with faster-whisper.

        Runs only when LRCLib / Genius / YouTube VTT all missed. We already
        fired off "No lyrics source" in the caller; this thread writes a
        word-level .ass tagged ``lyrics_source="whisper"`` so the splash
        badge can flag these as machine-transcribed (lower trust than a
        curated LRC / user-authored .ass).

        Uses Whisper's own word timestamps rather than re-aligning through
        wav2vec2. Whisper's timings are a touch coarser (~200ms) than
        forced alignment but the text it emits is a phoneme-level fiction
        anyway — pushing hallucinated words back through wav2vec2 would
        only hide the errors, not fix them.
        """
        try:
            self._emit_stage_notification(song_path, "Transcribing (Whisper)")
            _prewarm_stems(song_path)
            audio_path = _wait_for_alignment_audio(song_path)
            model = _get_whisper_model()
            if model is None:
                return
            segments_iter, info = model.transcribe(
                audio_path,
                word_timestamps=True,
                vad_filter=True,
            )
            segments = list(segments_iter)
            if not segments:
                logger.info("Whisper fallback: empty transcription for %s", song_path)
                self._emit_whisper_failure(song_path, "empty transcription")
                return
            lrc = _lrc_from_whisper_segments(segments)
            lang = getattr(info, "language", None)
            words: list[Word] = []
            for seg in segments:
                for w in seg.words or []:
                    text = (getattr(w, "word", "") or "").strip()
                    if not text or w.start is None or w.end is None:
                        continue
                    start = float(w.start)
                    end = float(w.end)
                    parts = _syllable_parts(text, lang, start, end)
                    words.append(Word(text=text, start=start, end=end, parts=parts))
            if not words or not lrc:
                logger.info("Whisper fallback: no usable words for %s", song_path)
                self._emit_whisper_failure(song_path, "no usable word timings")
                return
            bpm = _estimate_bpm(audio_path)
            ass = _words_to_ass_with_k_tags(words, lrc, params=_anim_params_for_bpm(bpm))
            if not ass:
                logger.info("Whisper fallback: ASS conversion failed for %s", song_path)
                self._emit_whisper_failure(song_path, "ASS conversion failed")
                return
            _write_ass_atomic(song_path, ass)
            if lang and self._db is not None:
                song_id = self._db.get_song_id_by_path(song_path)
                if song_id is not None:
                    try:
                        # Whisper ASR's language-ID is acoustic ground truth
                        # on the vocals stem; ranks above every text-derived
                        # signal but below the dedicated pre-alignment probes
                        # (whisper_probe_raw / _stems).
                        self._db.update_track_metadata_with_provenance(
                            song_id, "whisper_asr", {"language": lang}
                        )
                    except Exception:
                        logger.exception("failed to persist whisper language for %s", song_path)
            self._register_ass(
                song_path,
                lyrics_source="whisper",
                aligner_model=f"whisper-{WHISPER_FALLBACK_MODEL}",
                lyrics_sha=_lrc_sha(lrc),
            )
            logger.info(
                "Whisper: wrote word-level .ass for %s (lang=%s, model=%s)",
                os.path.basename(song_path),
                lang or "?",
                WHISPER_FALLBACK_MODEL,
            )
            try:
                self._events.emit(
                    "notification",
                    f"Auto-lyrics ready: {_title_from_filename(song_path)}",
                    "info",
                )
            except Exception:
                logger.exception("failed to emit whisper success notification")
        except Exception as e:
            logger.exception("Whisper fallback crashed for %s", song_path)
            self._emit_whisper_failure(song_path, f"{type(e).__name__}: {e}")

    def _emit_whisper_failure(self, song_path: str, detail: str) -> None:
        try:
            self._events.emit(
                "song_warning",
                {
                    "message": "No lyrics found",
                    "detail": f"Whisper fallback: {detail}.",
                    "song": os.path.basename(song_path),
                    "severity": "warning",
                },
            )
        except Exception:
            logger.exception("failed to emit whisper-failure song_warning")

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


def _ass_path(song_path: str) -> str:
    stem, _ext = os.path.splitext(song_path)
    return f"{stem}.ass"


# ----- LRCLib client -----


def _strip_variant_markers(title: str) -> str:
    """Trim trailing `(Instrumental)` / `[Karaoke]` / etc. from a track title.

    LRCLib/Genius index lyrics once per song regardless of mix; querying with
    a variant suffix drops otherwise-good matches. Called on the query only;
    never mutates the DB title. Returns the original string when no marker
    matches or stripping would yield an empty result.
    """
    stripped = _VARIANT_RE.sub("", title).strip()
    return stripped or title


def _fetch_lrclib(track: str, artist: str, duration: int | float | None) -> str | None:
    """Query LRCLib for syncedLyrics; None when none found or request failed."""
    track = _strip_variant_markers(track)
    get_params: dict[str, str | int] = {"track_name": track, "artist_name": artist}
    if duration:
        get_params["duration"] = int(duration)
    try:
        r = requests.get(f"{LRCLIB_BASE}/api/get", params=get_params, timeout=LRCLIB_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            synced = data.get("syncedLyrics")
            if synced:
                logger.info(
                    "LRCLib: hit /api/get track=%r artist=%r duration=%s",
                    track,
                    artist,
                    duration,
                )
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
                    logger.info("LRCLib: hit /api/search track=%r artist=%r", track, artist)
                    return synced
    except (requests.RequestException, ValueError) as e:
        logger.warning("LRCLib request failed: %s", e)
        return None
    logger.info("LRCLib: miss track=%r artist=%r duration=%s", track, artist, duration)
    return None


# ----- Genius client -----


def _fetch_genius(track: str, artist: str) -> str | None:
    """Return plain-text lyrics from Genius, or None on miss / missing token.

    Flow:
      1. GET /search?q=<artist> <track>  (Bearer auth).
      2. Pick the first hit whose ``primary_artist.name`` case-insensitively
         matches ``artist``.
      3. Scrape the public song page and extract text from the lyrics
         containers (``div[data-lyrics-container="true"]``), preserving line
         breaks from ``<br>`` tags and dropping annotation markup.

    Returns None when ``GENIUS_ACCESS_TOKEN`` is empty (opt-in feature), on
    any HTTP failure, or when no artist-matched hit is found.
    """
    if not GENIUS_ACCESS_TOKEN or not track or not artist:
        return None
    query = f"{artist} {_strip_variant_markers(track)}".strip()
    try:
        r = requests.get(
            f"{GENIUS_BASE}/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {GENIUS_ACCESS_TOKEN}"},
            timeout=GENIUS_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        hits = r.json().get("response", {}).get("hits", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning("Genius search failed: %s", e)
        return None
    artist_lc = artist.strip().lower()
    url = None
    for hit in hits:
        result = hit.get("result") or {}
        primary = (result.get("primary_artist") or {}).get("name", "")
        if primary.strip().lower() == artist_lc:
            url = result.get("url")
            break
    if not url:
        return None
    try:
        page = requests.get(url, timeout=GENIUS_TIMEOUT)
        if page.status_code != 200:
            return None
    except requests.RequestException as e:
        logger.warning("Genius page fetch failed: %s", e)
        return None
    return _extract_genius_lyrics(page.text)


_GENIUS_CONTAINER_RE = re.compile(
    r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_GENIUS_SECTION_HEADER_RE = re.compile(r"^\s*\[[^\]]*\]\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _extract_genius_lyrics(html: str) -> str | None:
    """Pull plain-text lyrics out of a Genius song page.

    Genius wraps lyric blocks in ``data-lyrics-container="true"`` divs and
    uses ``<br>`` for line breaks inside each block. Section headers like
    ``[Verse 1]`` are dropped; they aren't part of the sung content.
    Returns None when no container is found.
    """
    containers = _GENIUS_CONTAINER_RE.findall(html)
    if not containers:
        return None
    lines: list[str] = []
    for raw in containers:
        text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
        text = _HTML_TAG_RE.sub("", text)
        text = _GENIUS_SECTION_HEADER_RE.sub("", text)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return "\n".join(lines) if lines else None


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


def lrc_line_windows(lrc: str) -> list[tuple[float, float, str]]:
    """Parse LRC into ``(line_start, line_end, text)`` triples.

    ``line_end`` is the next line's start; the final line uses
    ``_LAST_LINE_HOLD_S``. Used by the aligner to confine per-line
    SequenceMatcher so repeated phrases can't steal anchors across
    lines.
    """
    entries = _parse_lrc(lrc)
    windows: list[tuple[float, float, str]] = []
    for i, (start, text) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else start + _LAST_LINE_HOLD_S
        windows.append((start, end, text))
    return windows


def _lrc_from_aligned_lines(words: list[Word], lines: list[str]) -> str | None:
    """Build an LRC from aligned word timings + known line structure.

    Used on the Genius fallback path: Genius gives us plain lyrics with line
    breaks but no timestamps, so after wav2vec2 returns per-word timings
    (1:1 with the reference tokens), we consume one line's worth of tokens
    at a time and use the first aligned word's start as the line's LRC time.
    Returns None when the aligner dropped so many words we can't scaffold
    any line.
    """
    entries: list[str] = []
    idx = 0
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        end_idx = min(idx + len(tokens), len(words))
        line_words = words[idx:end_idx]
        idx = end_idx
        if not line_words:
            continue
        start = max(0.0, line_words[0].start)
        mm = int(start // 60)
        ss = start - mm * 60
        entries.append(f"[{mm:02d}:{ss:05.2f}]{line}")
    return "\n".join(entries) if entries else None


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
# second on the CI/RPi box.
_BPM_ANALYSIS_DURATION_S = 60.0


def _estimate_bpm(audio_path: str) -> float | None:
    """Best-effort song tempo in BPM, or None if detection fails.

    Used only to pick decorative animation parameters - never in a timing
    path - so any failure falls through to a plain (un-pulsed) render.
    """
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
            # Whisper's per-word timings for this line drifted too far from
            # the LRC window to trust their absolute values. Keep per-word
            # highlighting by re-anchoring the line's tokens to the LRC
            # window with uniform durations - sync accuracy falls back to
            # line-level granularity (same as pre-whisperx baseline), but
            # the user still sees a smooth wipe instead of a frozen line.
            current_ass = _uniform_k_tokens(text.split(), start, end, params)
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


def _uniform_k_tokens(
    tokens: list[str], start: float, end: float, params: "_AnimParams | None" = None
) -> str:
    """Render ``tokens`` as \\kf tags spread evenly across ``[start, end]``.

    Fallback path when whisper's per-word timings can't be trusted for a
    line. The line itself is still time-accurate (LRC window), only the
    intra-line word wipe speed is estimated uniformly.
    """
    if not tokens:
        return ""
    duration = max(end - start, 0.01)
    per = duration / len(tokens)
    return " ".join(
        _k_token(Word(text=t, start=start + per * i, end=start + per * (i + 1)), start, params)
        for i, t in enumerate(tokens)
    )


def _k_token(word: Word, line_start_s: float = 0.0, params: _AnimParams | None = None) -> str:
    """ASS karaoke tags for one word.

    Emits ``\\kf`` (smooth left-to-right color wipe) instead of the older
    ``\\k`` (instant flip) so sung words fade into the secondary colour
    rather than popping. When ``word.parts`` is set we emit one ``\\kf``
    per part (per-char on the WhisperX path, per-syllable on the Whisper
    fallback path); otherwise a single ``\\kf`` covers the whole word.

    When ``params.pulse_pct`` exceeds 100 we also wrap the first glyph
    group in a ``\\t`` scale transform that pulses up and releases
    across the word's full time window - one pulse per word, not per
    part, so multi-syllable words don't strobe. ``\\t`` offsets are in
    milliseconds from the enclosing Dialogue event's start, hence the
    ``line_start_s`` argument.
    """
    pulse_tag = _pulse_tag(word, line_start_s, params)
    fills = _kf_fills(word)
    # Splice the pulse override into the first fill's override block so
    # that \kf and \t sit inside a single {...} group - libass parses this
    # cleanly and it keeps the tag count down for long lines.
    if pulse_tag and fills:
        fills = fills.replace("{", "{" + pulse_tag, 1)
    return fills


def _kf_fills(word: Word) -> str:
    """Sequence of ``{\\kfN}text`` groups for ``word`` (one per part)."""
    parts = word.parts
    if not parts:
        dur_cs = max(1, int(round((word.end - word.start) * 100)))
        return f"{{\\kf{dur_cs}}}{_escape_ass(word.text)}"
    out = []
    for p in parts:
        dur_cs = max(1, int(round((p.end - p.start) * 100)))
        out.append(f"{{\\kf{dur_cs}}}{_escape_ass(p.text)}")
    return "".join(out)


def _pulse_tag(word: Word, line_start_s: float, params: _AnimParams | None) -> str:
    """Build the ``\\t`` scale-pulse override for one word, empty when disabled.

    The pulse spans the whole word (not per-part) so multi-part words
    get a single scale bump that lines up with the word's onset instead
    of strobing once per character.
    """
    if params is None or params.pulse_pct <= 100:
        return ""
    dur_cs = max(1, int(round((word.end - word.start) * 100)))
    total_ms = dur_cs * 10
    off_ms = max(0, int(round((word.start - line_start_s) * 1000)))
    rise_ms = max(1, int(total_ms * params.pulse_rise_frac))
    rise_end = off_ms + rise_ms
    fall_end = off_ms + total_ms
    pct = params.pulse_pct
    return (
        f"\\t({off_ms},{rise_end},\\fscx{pct}\\fscy{pct})"
        f"\\t({rise_end},{fall_end},\\fscx100\\fscy100)"
    )


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
        logger.info(
            "VTT: no candidates for %s (preferred_lang=%s)",
            basename,
            preferred_lang,
        )
        return None
    candidates.sort()
    chosen = candidates[0]
    logger.info(
        "VTT: picked lang=%s from %d candidate(s) for %s (preferred_lang=%s, auto=%s)",
        chosen[3],
        len(candidates),
        basename,
        preferred_lang,
        chosen[1],
    )
    return chosen[4]


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


def _cleanup_yt_vtt(song_path: str, db=None) -> None:
    """Remove <stem>*.vtt after conversion and drop the matching DB rows.

    info.json is owned elsewhere: ``register_download`` consumes-then-
    deletes it for fresh yt-dlp downloads, and scanner-imported
    collections deliberately preserve it (user's own files). Lyrics
    pipeline has no business touching it here.
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

    if db is None:
        return
    try:
        song_id = db.get_song_id_by_path(song_path)
    except Exception:
        logger.exception("failed to look up song_id for artifact cleanup: %s", song_path)
        return
    if song_id is None:
        return
    try:
        db.delete_artifacts_by_role(song_id, "vtt")
    except Exception:
        logger.exception("failed to unregister vtt artifacts for song_id=%s", song_id)


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


def _whisper_fallback_enabled() -> bool:
    """Honour WHISPER_FALLBACK_MODEL opt-out. Default: enabled."""
    return WHISPER_FALLBACK_MODEL.lower() not in _WHISPER_OPT_OUT


def _get_whisper_model():
    """Lazy-load faster-whisper once per process. Returns None if unavailable.

    Import is deferred so a missing ``faster-whisper`` install doesn't
    crash the rest of the app — songs just fall through to the
    "no lyrics source" warning instead.
    """
    if not _whisper_fallback_enabled():
        return None
    with _whisper_model_lock:
        if _whisper_model_cache[0] is not None:
            return _whisper_model_cache[0]
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.warning(
                "Whisper fallback: faster-whisper not installed; "
                "no auto-lyrics will be generated for songs missing curated captions."
            )
            return None
        try:
            model = WhisperModel(WHISPER_FALLBACK_MODEL, device="auto", compute_type="int8")
        except Exception:
            logger.exception(
                "Whisper fallback: failed to load model %r on device=auto; disabling",
                WHISPER_FALLBACK_MODEL,
            )
            return None
        _whisper_model_cache[0] = model
        logger.info("Whisper fallback: loaded model=%s", WHISPER_FALLBACK_MODEL)
        return model


# Whisper language codes -> pyphen locale. Pyphen needs region-qualified
# codes for some locales ("pl_PL" not "pl"). For languages pyphen doesn't
# ship a dictionary for (e.g. Japanese/Chinese - handled differently
# anyway), _syllabify returns None and the renderer falls back to a
# single \kf per word.
_PYPHEN_LANG_MAP = {
    "pl": "pl_PL",
    "en": "en_US",
    "de": "de_DE",
    "es": "es_ES",
    "fr": "fr_FR",
    "it": "it_IT",
    "pt": "pt_PT",
    "nl": "nl_NL",
    "sv": "sv",
    "no": "nb_NO",
    "nn": "nn_NO",
    "da": "da_DK",
    "fi": "fi_FI",
    "cs": "cs_CZ",
    "sk": "sk_SK",
    "ru": "ru_RU",
    "uk": "uk_UA",
    "hu": "hu_HU",
    "ro": "ro_RO",
    "hr": "hr_HR",
    "sl": "sl_SI",
    "lt": "lt_LT",
    "lv": "lv_LV",
    "et": "et_EE",
    "ca": "ca",
    "gl": "gl",
    "eu": "eu",
    "bg": "bg",
    "el": "el_GR",
    "tr": "tr_TR",
}

_pyphen_cache: dict[str, object] = {}


def _syllabify(word: str, language: str | None) -> list[tuple[int, int]] | None:
    """Return syllable spans ``[(start_char_idx, end_char_idx), ...]`` for ``word``.

    Uses pyphen (Hunspell hyphenation dictionaries). Returns ``None`` if
    pyphen isn't installed, the language has no dictionary, or the word
    has no internal hyphenation point (monosyllabic / too short / all
    non-alphabetic). Callers treat ``None`` as "render this word as a
    single ``\\kf``".

    Spans are half-open: ``word[start:end]`` is the syllable's text. This
    keeps the caller arithmetic consistent with Python slicing and lets
    us reconstruct the full word via concatenation.
    """
    if not word or not language:
        return None
    try:
        import pyphen
    except ImportError:
        return None
    locale = _PYPHEN_LANG_MAP.get(language.lower(), language)
    dic = _pyphen_cache.get(locale)
    if dic is None:
        try:
            if locale not in pyphen.LANGUAGES:
                # Try the bare language code as a last-ditch fallback.
                short = language.lower().split("_")[0]
                if short in pyphen.LANGUAGES:
                    locale = short
                else:
                    _pyphen_cache[locale] = False  # sentinel: no dict
                    return None
            dic = pyphen.Pyphen(lang=locale)
            _pyphen_cache[locale] = dic
        except (KeyError, OSError):
            _pyphen_cache[locale] = False
            return None
    if dic is False:
        return None
    positions = dic.positions(word)  # type: ignore[union-attr]
    if not positions:
        return None
    spans: list[tuple[int, int]] = []
    prev = 0
    for p in positions:
        spans.append((prev, p))
        prev = p
    spans.append((prev, len(word)))
    return spans


def _syllable_parts(
    word: str, language: str | None, start: float, end: float
) -> tuple["WordPart", ...] | None:
    """Build per-syllable ``WordPart`` spans for ``word``.

    Used by the Whisper-ASR fallback where we only have word-level
    timings: pyphen splits the word, then the word's duration is sliced
    proportionally to each syllable's character length. Returns ``None``
    for monosyllabic words or unsupported languages so the renderer
    falls back to a single ``\\kf`` per word (same UX as before).
    """
    spans = _syllabify(word, language)
    if not spans or len(spans) < 2:
        return None
    total_chars = spans[-1][1] - spans[0][0]
    if total_chars <= 0:
        return None
    duration = max(end - start, 0.01)
    parts: list[WordPart] = []
    cursor = start
    for i, (a, b) in enumerate(spans):
        text = word[a:b]
        if not text:
            continue
        if i == len(spans) - 1:
            part_end = end
        else:
            part_end = cursor + duration * (b - a) / total_chars
        parts.append(WordPart(text=text, start=cursor, end=part_end))
        cursor = part_end
    return tuple(parts) if len(parts) >= 2 else None


def _lrc_from_whisper_segments(segments) -> str:
    """Build a synthetic LRC (one line per whisper segment).

    ``_words_to_ass_with_k_tags`` needs an LRC string to locate line
    boundaries and per-line start times; whisper's segments approximate
    spoken lines well enough for that. Text is stripped; empty segments
    are dropped so a leading silence doesn't produce a blank LRC line
    that would offset word-to-line assignment.
    """
    lines = []
    for seg in segments:
        text = (getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(seg, "start", 0.0) or 0.0)
        minutes = int(start // 60)
        seconds = start - minutes * 60
        lines.append(f"[{minutes:02d}:{seconds:05.2f}]{text}")
    return "\n".join(lines)
