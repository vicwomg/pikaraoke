"""Whisper language-ID probes for the lyrics pipeline (US-43).

Two probes live here, sharing cache helpers:

Tier 2a — **raw audio, fast-path** (``probe_language``). Fires when
Tier 1's text-consensus classifier returns no verdict. Windowed 30s
probe at 50% of track duration with a 30% fallback window; never waits
on demucs. Budget: <=5s warm / <=15s cold.

Tier 2b — **vocals stem, post-demucs** (``probe_language_whole_song``).
Fires on ``stems_ready`` to re-validate 2a (and any text-based winner).
Runs ``detect_language`` over the whole song with VAD filter enabled:
demucs already stripped the instruments, so the remaining audio is
dense vocal content — averaging probabilities across every 30s mel
segment gives meaningfully higher confidence than a single window on
the raw mix. Off the fast path, so wall-clock cost isn't critical.

Both probes cache per ``audio_sha256`` in the ``metadata`` KV table
under distinct prefixes (``whisper_probe_raw:`` and
``whisper_probe_stems:``). Value is a small JSON blob
``{"lang": "pl", "conf": 0.87}``; ``lang=null`` cached on
inconclusive outcomes so repeat boots don't re-pay the probe. No new
schema, no new DDL.

Both share the faster-whisper singleton from ``lyrics._get_whisper_model``
via the ``get_model`` injection point — the ``WhisperXAligner`` in
``lyrics_align`` only loads wav2vec2 for forced alignment and doesn't
expose a whisper model to share.
"""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Sampling rate we pass to ``decode_audio``; must match what
# ``WhisperModel.detect_language`` expects.
_SAMPLE_RATE = 16000

# Mel segment length used by faster-whisper. 30s is both the minimum
# meaningful window (one full mel segment) and the maximum the model
# actually attends to per ``language_detection_segments=1``.
_PROBE_WINDOW_S = 30.0

# Centre offset (fraction of duration) for the primary probe window.
# 50% intentionally dodges the instrumental intro/outro that bracket
# most pop tracks.
_PRIMARY_OFFSET = 0.50

# Fallback window centre when the primary window is low-confidence.
# 30% keeps the second probe disjoint from the first in most songs
# (3-5 min duration) without landing in a fade-out.
_FALLBACK_OFFSET = 0.30

# Accept a single-window verdict at or above this confidence without
# running the second probe. Below this, re-probe at the fallback
# offset and require the two windows to agree.
_MIN_SINGLE_WINDOW_CONFIDENCE = 0.5

# Tier 2b: how many 30s mel segments to let ``detect_language`` average
# over. Capped to keep memory bounded; faster-whisper truncates the
# VAD-concatenated audio to ``segments * n_samples``, so 20 segments
# covers ~10 min of *sung content* (post-VAD) — more than any song.
_WHOLE_SONG_MAX_SEGMENTS = 20

# Tier 2b: below this aggregate confidence the stem probe is treated as
# inconclusive. Lower than Tier 2a's single-window threshold because the
# whole-song probe averages across many segments — a value this low
# usually means "no vocal content detected on the stem" (demucs
# produced garbage, or the track is purely instrumental).
_WHOLE_SONG_MIN_CONFIDENCE = 0.3

# Cache key prefixes in ``db.metadata``. Distinct per tier so 2a and 2b
# verdicts co-exist for the same audio_sha256.
_TIER2A_PREFIX = "whisper_probe_raw"
_TIER2B_PREFIX = "whisper_probe_stems"


def _cache_key(prefix: str, audio_sha256: str) -> str:
    return f"{prefix}:{audio_sha256}"


def _read_cached_verdict(
    cache_get: Callable[[str], str | None], prefix: str, audio_sha256: str
) -> tuple[str | None, bool]:
    """Return ``(language_or_None, cache_hit)`` for a previously probed sha.

    A ``cache_hit=True`` with ``language=None`` means "already probed,
    inconclusive" — the caller must still honour the cache and skip the
    probe rather than re-running. Invalid JSON is treated as a miss so
    a corrupted row self-heals on the next pass.
    """
    raw = cache_get(_cache_key(prefix, audio_sha256))
    if not raw:
        return None, False
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None, False
    if not isinstance(data, dict):
        return None, False
    lang = data.get("lang")
    return (lang if isinstance(lang, str) else None), True


def read_cached_verdict(
    cache_get: Callable[[str], str | None],
    audio_sha256: str,
    *,
    prefix: str = _TIER2A_PREFIX,
) -> tuple[str | None, bool]:
    """Public cache-read shim. Default prefix is Tier 2a for back-compat."""
    return _read_cached_verdict(cache_get, prefix, audio_sha256)


def _write_cache(
    cache_set: Callable[[str, str], None],
    prefix: str,
    audio_sha256: str,
    lang: str | None,
    confidence: float | None,
) -> None:
    payload = {"lang": lang, "conf": confidence}
    try:
        cache_set(_cache_key(prefix, audio_sha256), json.dumps(payload, sort_keys=True))
    except Exception:
        logger.exception("%s: failed to cache sha=%s", prefix, audio_sha256[:12])


def _lang_base(lang: str | None) -> str | None:
    """Normalize ``en-US`` / ``pl_PL`` / ``zh-TW`` to the primary subtag."""
    if not lang:
        return None
    return lang.split("-", 1)[0].split("_", 1)[0].lower()


def _slice_window(audio, total_samples: int, duration_s: float, offset_frac: float):
    """Return a contiguous ``_PROBE_WINDOW_S``-second slice centred on ``offset_frac``.

    Clamps to the array bounds; for tracks shorter than the window we
    return whatever is available (callers handle the ``.size == 0`` case).
    """
    window_samples = int(_PROBE_WINDOW_S * _SAMPLE_RATE)
    centre = int(duration_s * offset_frac * _SAMPLE_RATE)
    start = max(0, centre - window_samples // 2)
    end = min(total_samples, start + window_samples)
    # If clamping on the right truncated the window, shift start back
    # to keep a full 30s slice when the underlying audio is long enough.
    if end - start < window_samples and end == total_samples:
        start = max(0, end - window_samples)
    return audio[start:end]


def _probe_one(model: Any, audio_slice) -> tuple[str | None, float]:
    """Run ``WhisperModel.detect_language`` on one window; normalize output."""
    if audio_slice.size == 0:
        return None, 0.0
    lang, prob, _all = model.detect_language(audio=audio_slice)
    return _lang_base(lang), float(prob)


def probe_language(
    *,
    audio_path: str,
    audio_sha256: str,
    duration_seconds: float | None,
    get_model: Callable[[], Any],
    cache_get: Callable[[str], str | None],
    cache_set: Callable[[str, str], None],
    decode_audio_fn: Callable[..., Any] | None = None,
) -> str | None:
    """Run the Tier 2a Whisper language-ID probe with per-sha caching.

    Returns the detected primary language subtag (``"pl"``, ``"en"``,
    ``"ja"``) or ``None`` when the probe is inconclusive. ``None``
    outcomes are cached too so repeated Tier-1 misses on the same audio
    don't re-run Whisper every boot.

    Parameters are passed through an injection interface rather than
    imported from ``lyrics.py`` so the probe module is independently
    unit-testable — tests pass in a mock ``get_model`` and a captured
    ``decode_audio_fn`` without ever loading faster-whisper.
    """
    cached, hit = _read_cached_verdict(cache_get, _TIER2A_PREFIX, audio_sha256)
    if hit:
        logger.info("whisper_probe_raw: cache hit sha=%s lang=%s", audio_sha256[:12], cached)
        return cached

    model = get_model()
    if model is None:
        logger.info("whisper_probe_raw: model unavailable; skip sha=%s", audio_sha256[:12])
        return None

    decode = decode_audio_fn
    if decode is None:
        try:
            # Late import: the rest of pikaraoke tolerates a missing
            # faster-whisper install, so we mirror that policy here.
            from faster_whisper.audio import decode_audio as decode
        except ImportError:
            logger.info("whisper_probe_raw: faster-whisper not installed; skip")
            return None

    t0 = time.monotonic()
    try:
        audio = decode(audio_path, sampling_rate=_SAMPLE_RATE)
    except Exception:
        logger.exception("whisper_probe_raw: decode failed for %s", audio_path)
        return None

    total_samples = int(getattr(audio, "shape", (len(audio),))[0])
    if total_samples == 0:
        _write_cache(cache_set, _TIER2A_PREFIX, audio_sha256, None, None)
        return None

    # DB-recorded duration is authoritative when present (some video
    # containers lie about audio length in their first frame); fall back
    # to the decoded sample count otherwise.
    duration_s = (
        float(duration_seconds)
        if duration_seconds and duration_seconds > 0
        else total_samples / _SAMPLE_RATE
    )

    lang1, conf1 = _probe_one(
        model, _slice_window(audio, total_samples, duration_s, _PRIMARY_OFFSET)
    )
    logger.info(
        "whisper_probe_raw: primary window=%.0f%% lang=%s conf=%.3f sha=%s",
        _PRIMARY_OFFSET * 100,
        lang1,
        conf1,
        audio_sha256[:12],
    )

    if lang1 and conf1 >= _MIN_SINGLE_WINDOW_CONFIDENCE:
        elapsed = time.monotonic() - t0
        logger.info(
            "whisper_probe_raw: accepted lang=%s conf=%.3f sha=%s elapsed=%.2fs " "(single window)",
            lang1,
            conf1,
            audio_sha256[:12],
            elapsed,
        )
        _write_cache(cache_set, _TIER2A_PREFIX, audio_sha256, lang1, conf1)
        return lang1

    # Low-confidence primary: re-probe at the fallback offset and require
    # the two windows to agree on the same primary subtag. This is the
    # "instrumental-heavy" guard — a track whose primary window landed on
    # an instrumental bridge usually returns a different dominant
    # language than one with sung content, so disagreement between the
    # two windows flags "don't trust either" rather than letting a noisy
    # single window set the DB.
    lang2, conf2 = _probe_one(
        model, _slice_window(audio, total_samples, duration_s, _FALLBACK_OFFSET)
    )
    logger.info(
        "whisper_probe_raw: fallback window=%.0f%% lang=%s conf=%.3f sha=%s",
        _FALLBACK_OFFSET * 100,
        lang2,
        conf2,
        audio_sha256[:12],
    )
    elapsed = time.monotonic() - t0

    if lang1 and lang2 and lang1 == lang2:
        logger.info(
            "whisper_probe_raw: majority-vote accepted lang=%s confs=%.3f/%.3f "
            "sha=%s elapsed=%.2fs",
            lang1,
            conf1,
            conf2,
            audio_sha256[:12],
            elapsed,
        )
        _write_cache(cache_set, _TIER2A_PREFIX, audio_sha256, lang1, min(conf1, conf2))
        return lang1

    logger.info(
        "whisper_probe_raw: no verdict windows=(%s/%.3f, %s/%.3f) sha=%s "
        "elapsed=%.2fs (defer to Tier 3)",
        lang1,
        conf1,
        lang2,
        conf2,
        audio_sha256[:12],
        elapsed,
    )
    _write_cache(cache_set, _TIER2A_PREFIX, audio_sha256, None, None)
    return None


def probe_language_whole_song(
    *,
    audio_path: str,
    audio_sha256: str,
    get_model: Callable[[], Any],
    cache_get: Callable[[str], str | None],
    cache_set: Callable[[str, str], None],
    decode_audio_fn: Callable[..., Any] | None = None,
    max_segments: int = _WHOLE_SONG_MAX_SEGMENTS,
    min_confidence: float = _WHOLE_SONG_MIN_CONFIDENCE,
) -> str | None:
    """Tier 2b: whole-song Whisper language-ID on an isolated vocals stem.

    faster-whisper's ``detect_language(language_detection_segments=N,
    vad_filter=True)`` concatenates every VAD-detected speech chunk in
    the input, truncates to ``N * 30s``, then averages per-segment
    language probabilities. On a demucs-isolated vocals stem this is
    ~all sung content in a typical 3-5 min song, averaged across 6-10
    mel segments — meaningfully higher confidence than any single
    window on the raw mix could give.

    Returns the detected primary subtag, or ``None`` when the aggregate
    confidence falls below ``min_confidence`` (treated as "no vocal
    content detected on the stem" — demucs produced garbage, or the
    track is purely instrumental). Both outcomes are cached so the
    probe runs at most once per unique ``audio_sha256``.

    Fires only on ``stems_ready``; does NOT wait on demucs from inside.
    Wall-clock cost isn't on the fast path (the line-level ``.ass`` has
    already landed), so no window tricks — we always process the whole
    stem.
    """
    cached, hit = _read_cached_verdict(cache_get, _TIER2B_PREFIX, audio_sha256)
    if hit:
        logger.info(
            "whisper_probe_stems: cache hit sha=%s lang=%s",
            audio_sha256[:12],
            cached,
        )
        return cached

    model = get_model()
    if model is None:
        logger.info("whisper_probe_stems: model unavailable; skip sha=%s", audio_sha256[:12])
        return None

    decode = decode_audio_fn
    if decode is None:
        try:
            from faster_whisper.audio import decode_audio as decode
        except ImportError:
            logger.info("whisper_probe_stems: faster-whisper not installed; skip")
            return None

    t0 = time.monotonic()
    try:
        audio = decode(audio_path, sampling_rate=_SAMPLE_RATE)
    except Exception:
        logger.exception("whisper_probe_stems: decode failed for %s", audio_path)
        return None

    total_samples = int(getattr(audio, "shape", (len(audio),))[0])
    if total_samples == 0:
        _write_cache(cache_set, _TIER2B_PREFIX, audio_sha256, None, None)
        return None

    try:
        lang, conf, _all = model.detect_language(
            audio=audio,
            vad_filter=True,
            language_detection_segments=max_segments,
        )
    except Exception:
        logger.exception("whisper_probe_stems: detect_language failed for %s", audio_path)
        return None

    elapsed = time.monotonic() - t0
    lang = _lang_base(lang)
    conf = float(conf)

    if not lang or conf < min_confidence:
        logger.info(
            "whisper_probe_stems: inconclusive lang=%s conf=%.3f (< %.2f) sha=%s " "elapsed=%.2fs",
            lang,
            conf,
            min_confidence,
            audio_sha256[:12],
            elapsed,
        )
        _write_cache(cache_set, _TIER2B_PREFIX, audio_sha256, None, None)
        return None

    logger.info(
        "whisper_probe_stems: accepted lang=%s conf=%.3f sha=%s elapsed=%.2fs " "(whole-song, vad)",
        lang,
        conf,
        audio_sha256[:12],
        elapsed,
    )
    _write_cache(cache_set, _TIER2B_PREFIX, audio_sha256, lang, conf)
    return lang
