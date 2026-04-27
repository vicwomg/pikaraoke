"""Vocal-onset detection for the LRC->audio line-anchor pass.

Returns per-phrase vocal onsets the DP solver in ``lyrics_align`` uses
as anchors. Two complementary detectors run on the same audio path
and their outputs merge into a single sorted onset list:

* **Silero VAD** — chunked through 60 s windows so the model resets
  state between sections (silero's continuous-state inference fails
  badly on a multi-minute song with a long instrumental break, even
  on the Demucs vocals stem). Surfaces phrase-level onsets where the
  vocal classifier agrees with what's audible.
* **ffmpeg silencedetect** — fills silero's blind spots. Some sustained
  rock-belt vocals (Total Eclipse 3:28-4:15) classify as non-speech
  for silero but are clearly audio crossings; silencedetect catches
  them. Onsets whose true audio sustain is below ``_MIN_AUDIO_S`` are
  filtered (e.g. silencedetect's 1 ms artefact between back-to-back
  silence intervals).

Merged result: ``[(onset, next_onset_in_merged_list), ...]`` sorted by
onset. Sustain = next_onset - onset. Final entry's next_onset is the
audio duration. Empty list on probe failure.

When silero is missing (``[align]`` extra not installed) the module
falls back to silencedetect-only. Caller behaviour degrades gracefully:
the DP still runs, just with sparser anchors.
"""

import logging
import re
import shutil
import subprocess
from threading import Lock

logger = logging.getLogger(__name__)

# Silero defaults are tuned for clean speech; on the Demucs vocals
# stem we tighten min-speech to drop drum/percussion bleed-through and
# loosen min-silence so back-to-back sung phrases don't merge into one
# anchor. Threshold 0.5 is the silero default; lowering it to 0.4 was
# tried during fixture capture and over-emitted false positives during
# instrumental sustains.
_VAD_THRESHOLD = 0.5
_VAD_MIN_SPEECH_MS = 300
_VAD_MIN_SILENCE_MS = 200
_VAD_SAMPLE_RATE = 16000

# Chunk size for silero. The model carries internal state across the
# input, and on long files (>5 min) with a long instrumental gap, that
# state fails to recover after the gap and silero stops detecting
# audible regions for the rest of the song. Re-running per ~60 s window
# (with a small overlap to recover boundary phrases) sidesteps this.
_VAD_CHUNK_S = 60.0
_VAD_CHUNK_OVERLAP_S = 5.0

# silencedetect threshold + minimum-duration. -30 dBFS catches
# sustained vocals without being so sensitive that breath noise
# qualifies. 0.5 s minimum-silence avoids treating brief consonant
# gaps as section breaks.
_FALLBACK_SILENCE_DB = -30
_FALLBACK_SILENCE_MIN_S = 0.5

# Drop silencedetect onsets whose actual audio sustain is below this.
# Without the floor, the back-to-back-silence artefact (silence_end[i]
# almost equal to silence_start[i+1]) leaks 1 ms "audible" regions
# into the anchor list - the DP would happily anchor an LRC line to
# a noise spike inside an instrumental section.
_MIN_AUDIO_S = 0.5

# Onsets within this distance collapse into one (silero's per-phrase
# onset and silencedetect's silence_end frequently report within a
# few hundred ms of each other for the same phrase).
_MERGE_TOLERANCE_S = 0.5

_model = None
_model_unavailable = False
_model_lock = Lock()


def _ensure_model() -> object | None:
    """Load the silero JIT model once per process. Returns None on failure.

    Safe to call from any thread; concurrent first-call invocations
    coalesce on the lock so the model only loads once. After failure
    further calls return None immediately (the caller falls back to
    ffmpeg silencedetect).
    """
    global _model, _model_unavailable
    if _model is not None or _model_unavailable:
        return _model
    with _model_lock:
        if _model is not None or _model_unavailable:
            return _model
        try:
            from silero_vad import load_silero_vad

            _model = load_silero_vad()
            logger.info("vad_probe: silero model ready")
        except ImportError:
            logger.info(
                "vad_probe: silero-vad not installed; falling back to ffmpeg silencedetect"
            )
            _model_unavailable = True
        except Exception:
            logger.warning("vad_probe: silero model load failed", exc_info=True)
            _model_unavailable = True
    return _model


def list_vocal_onsets(audio_path: str) -> list[tuple[float, float]]:
    """Return ``[(onset_s, next_onset_s), ...]`` for vocal phrases in audio.

    Each entry's second element is the start time of the next phrase
    in the merged list, so ``next_onset - onset`` is the audio window
    the DP solver treats as available sustain. The final entry's
    ``next_onset`` is the audio duration. Empty list on probe failure.

    Combines silero VAD (primary) with silencedetect (gap filler) on
    the same audio path. See module docstring.
    """
    silero_starts, audio_duration = _silero_onset_starts(audio_path)
    silence_pairs = _silencedetect_onset_pairs(audio_path)

    if audio_duration is None and silence_pairs:
        # Conservative duration when silero unavailable: last
        # silencedetect onset's audio end.
        audio_duration = max(end for _, end in silence_pairs)
    if audio_duration is None:
        return []

    silencedetect_starts = [
        onset for onset, end in silence_pairs if (end - onset) >= _MIN_AUDIO_S
    ]
    merged = _merge_starts(silero_starts + silencedetect_starts)
    if not merged:
        return []
    return _to_onset_pairs(merged, audio_duration)


def _silero_onset_starts(audio_path: str) -> tuple[list[float], float | None]:
    """Run silero on ``audio_path`` in chunks. Returns (sorted_starts, duration_s).

    Returns ``([], None)`` when silero isn't usable - caller falls back
    to silencedetect alone, with audio duration sourced from there.
    """
    model = _ensure_model()
    if model is None:
        return [], None
    try:
        import librosa
        import torch
        from silero_vad import get_speech_timestamps
    except ImportError:
        return [], None
    try:
        samples, _sr = librosa.load(audio_path, sr=_VAD_SAMPLE_RATE, mono=True)
        audio = torch.from_numpy(samples).float()
    except (FileNotFoundError, RuntimeError, ValueError):
        logger.warning("vad_probe: audio decode failed for %s", audio_path, exc_info=True)
        return [], None
    duration = float(len(audio)) / _VAD_SAMPLE_RATE if len(audio) else 0.0
    if duration <= 0:
        return [], duration

    chunk_samples = int(_VAD_CHUNK_S * _VAD_SAMPLE_RATE)
    step_samples = int((_VAD_CHUNK_S - _VAD_CHUNK_OVERLAP_S) * _VAD_SAMPLE_RATE)
    starts: list[float] = []
    for offset in range(0, len(audio), step_samples):
        chunk = audio[offset : offset + chunk_samples]
        if len(chunk) < _VAD_SAMPLE_RATE * 2:  # skip <2 s tail
            break
        offset_s = offset / _VAD_SAMPLE_RATE
        try:
            model.reset_states()
            segments = get_speech_timestamps(
                chunk,
                model,
                threshold=_VAD_THRESHOLD,
                sampling_rate=_VAD_SAMPLE_RATE,
                min_speech_duration_ms=_VAD_MIN_SPEECH_MS,
                min_silence_duration_ms=_VAD_MIN_SILENCE_MS,
                return_seconds=True,
            )
        except (RuntimeError, ValueError):
            logger.warning("vad_probe: silero chunk failed", exc_info=True)
            continue
        for seg in segments:
            local = float(seg["start"])
            if offset > 0 and local < _VAD_CHUNK_OVERLAP_S / 2:
                # Already covered by the previous chunk's tail.
                continue
            starts.append(local + offset_s)
    starts.sort()
    return starts, duration


def _silencedetect_onset_pairs(audio_path: str) -> list[tuple[float, float]]:
    """Return ``[(onset, audio_end), ...]`` from ffmpeg silencedetect.

    Each pair represents one continuous audible region. Filtering on
    ``audio_end - onset`` lets the caller drop ms-long artefacts.
    """
    if not shutil.which("ffmpeg"):
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                audio_path,
                "-af",
                f"silencedetect=n={_FALLBACK_SILENCE_DB}dB:d={_FALLBACK_SILENCE_MIN_S}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    silence_starts = [
        float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", proc.stderr)
    ]
    silence_ends = [
        float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", proc.stderr)
    ]
    pairs: list[tuple[float, float]] = []
    # silence_end[i] = onset of audible region; silence_start[j] where
    # j is the smallest index with silence_start[j] >= silence_end[i] is
    # the end of that region.
    for end in silence_ends:
        next_silence = next((s for s in silence_starts if s >= end), None)
        audio_end = next_silence if next_silence is not None else float("inf")
        pairs.append((end, audio_end))
    return pairs


def _merge_starts(starts: list[float]) -> list[float]:
    """Sort and dedup onset starts within ``_MERGE_TOLERANCE_S``."""
    if not starts:
        return []
    sorted_starts = sorted(starts)
    merged = [sorted_starts[0]]
    for t in sorted_starts[1:]:
        if t - merged[-1] < _MERGE_TOLERANCE_S:
            continue
        merged.append(t)
    return merged


def _to_onset_pairs(
    starts: list[float], audio_duration: float
) -> list[tuple[float, float]]:
    """Zip a sorted list of onsets with each one's *next* onset."""
    if not starts:
        return []
    pairs: list[tuple[float, float]] = []
    for i, onset in enumerate(starts):
        next_onset = starts[i + 1] if i + 1 < len(starts) else audio_duration
        pairs.append((float(onset), float(next_onset)))
    return pairs
