"""Demucs-based vocal separation with streaming output and disk cache.

Separates vocals from music using Meta's Demucs model (htdemucs).
Writes both vocal and instrumental stems progressively as segments complete,
enabling playback before the full track is processed. Results are cached
on disk so previously-processed songs play instantly.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import soundfile as sf
import torch
from tqdm import tqdm

CACHE_DIR = os.path.expanduser("~/.pikaraoke-cache")

# Global model cache — loaded once, reused across calls
_model = None
_model_lock = threading.Lock()
_device = None


def _get_device() -> torch.device:
    """Select best available device: MPS (Apple Silicon) > CUDA > CPU."""
    global _device
    if _device is not None:
        return _device
    if torch.backends.mps.is_available():
        _device = torch.device("mps")
    elif torch.cuda.is_available():
        _device = torch.device("cuda")
    else:
        _device = torch.device("cpu")
    logging.info(f"Demucs using device: {_device}")
    return _device


def _get_model():
    """Load and cache the Demucs htdemucs model."""
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        from demucs.pretrained import get_model

        logging.info("Loading Demucs model (htdemucs)...")
        device = _get_device()
        _model = get_model("htdemucs")
        _model.to(device)
        logging.info(f"Demucs model loaded. Sources: {_model.sources}")
        return _model


def _write_wav_header(f, sr: int, channels: int, total_samples: int) -> None:
    """Write a WAV header with the full expected size."""
    data_size = total_samples * channels * 2  # 16-bit
    f.write(b"RIFF")
    f.write(struct.pack("<I", 36 + data_size))
    f.write(b"WAVE")
    f.write(b"fmt ")
    f.write(struct.pack("<IHHIIHH", 16, 1, channels, sr, sr * channels * 2, channels * 2, 16))
    f.write(b"data")
    f.write(struct.pack("<I", data_size))


# --- Cache functions ---
#
# Cache layout per song (keyed by SHA256 of decoded PCM):
#   ~/.pikaraoke-cache/<sha256>/
#     vocals.wav.partial        — while Demucs is writing (tier 3, tail-streamed)
#     vocals.wav                — after Demucs completes (tier 2)
#     vocals.mp3                — after background encode (tier 1, preferred)
#     (instrumental.* mirrors)
#
# Lookup preference: MP3 > WAV > live Demucs. On startup, stray *.partial
# files from a crashed run are purged.


def get_cache_key(file_path: str) -> str:
    """Compute SHA256 of the source media file's bytes.

    Content-addressable so re-encoded files (different container, same audio)
    still hit cache when bytes match. Reading the mp4 directly is ~10x cheaper
    than extracting WAV first, and lets the caller kick off the pipeline
    without a synchronous ffmpeg extract step on the main thread.
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)  # 1MB chunks
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _cache_dir(cache_key: str) -> str:
    return os.path.join(CACHE_DIR, cache_key)


def get_cached_stems(cache_key: str) -> tuple[str, str, str] | None:
    """Return (vocals_path, instrumental_path, format) from best available tier.

    format is "mp3" or "wav". Returns None if nothing is cached.
    """
    d = _cache_dir(cache_key)
    mp3_v = os.path.join(d, "vocals.mp3")
    mp3_i = os.path.join(d, "instrumental.mp3")
    if os.path.isfile(mp3_v) and os.path.isfile(mp3_i):
        logging.info(f"Demucs cache hit (mp3): {cache_key[:12]}...")
        return mp3_v, mp3_i, "mp3"

    wav_v = os.path.join(d, "vocals.wav")
    wav_i = os.path.join(d, "instrumental.wav")
    if os.path.isfile(wav_v) and os.path.isfile(wav_i):
        logging.info(f"Demucs cache hit (wav): {cache_key[:12]}...")
        return wav_v, wav_i, "wav"

    return None


def partial_stem_paths(cache_key: str) -> tuple[str, str]:
    """Return the .partial paths Demucs writes to while streaming."""
    d = _cache_dir(cache_key)
    os.makedirs(d, exist_ok=True)
    return (
        os.path.join(d, "vocals.wav.partial"),
        os.path.join(d, "instrumental.wav.partial"),
    )


def finalize_partial_stems(cache_key: str) -> tuple[str, str]:
    """Rename .partial files to their final .wav names. Returns final paths."""
    d = _cache_dir(cache_key)
    partials = partial_stem_paths(cache_key)
    finals = (os.path.join(d, "vocals.wav"), os.path.join(d, "instrumental.wav"))
    for p, f in zip(partials, finals):
        if os.path.exists(p):
            os.replace(p, f)
    return finals


def cleanup_stale_partials() -> None:
    """Remove any *.partial files left over from a crashed Demucs run."""
    if not os.path.isdir(CACHE_DIR):
        return
    for entry in os.listdir(CACHE_DIR):
        d = os.path.join(CACHE_DIR, entry)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.endswith(".partial"):
                try:
                    os.remove(os.path.join(d, name))
                    logging.info(f"Removed stale partial: {entry}/{name}")
                except OSError:
                    pass


def encode_mp3_in_background(cache_key: str, bitrate: str = "320k") -> None:
    """Encode cached WAVs to MP3 in a background thread, then delete WAVs.

    No-op if MP3s already exist or WAVs are missing. On Unix the WAV delete
    does not affect in-flight HTTP tail responses (open fds keep reading).
    """
    import subprocess as sp

    d = _cache_dir(cache_key)
    wav_v = os.path.join(d, "vocals.wav")
    wav_i = os.path.join(d, "instrumental.wav")
    mp3_v = os.path.join(d, "vocals.mp3")
    mp3_i = os.path.join(d, "instrumental.mp3")

    if os.path.isfile(mp3_v) and os.path.isfile(mp3_i):
        return  # already encoded
    if not (os.path.isfile(wav_v) and os.path.isfile(wav_i)):
        return  # nothing to encode

    def run() -> None:
        for wav, mp3 in [(wav_v, mp3_v), (wav_i, mp3_i)]:
            if os.path.isfile(mp3):
                continue
            tmp = mp3 + ".partial"
            try:
                sp.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        wav,
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        bitrate,
                        "-f",
                        "mp3",
                        tmp,
                    ],
                    check=True,
                    capture_output=True,
                )
                os.replace(tmp, mp3)
            except sp.CalledProcessError as e:
                stderr = e.stderr.decode(errors="replace") if e.stderr else ""
                logging.error(f"MP3 encode failed for {wav} (exit {e.returncode}): {stderr}")
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return
            except OSError:
                logging.exception(f"MP3 encode failed for {wav}")
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return
        # Both MP3s ready — remove WAVs. Unix keeps existing fds alive.
        for wav in (wav_v, wav_i):
            try:
                os.remove(wav)
            except OSError:
                pass
        logging.info(f"MP3 cache ready, WAVs removed: {cache_key[:12]}...")

    threading.Thread(target=run, daemon=True).start()


# --- Separation ---


def separate_stems(
    input_wav: str,
    output_vocals: str,
    output_instrumental: str,
    ready_event: threading.Event | None = None,
    progress_callback: Callable[[float, float], None] | None = None,
) -> bool:
    """Separate audio into vocal and instrumental stems using Demucs.

    Processes audio in segments (~7.8s each) and writes completed segments
    to both output WAVs progressively. Sets ready_event after the first
    segment of both files is written.

    Args:
        input_wav: Path to input WAV file.
        output_vocals: Path to output vocals WAV file.
        output_instrumental: Path to output instrumental WAV file.
        ready_event: Optional event set when first segment is ready for playback.
        progress_callback: Optional callback(processed_seconds, total_seconds)
            invoked after each segment flush. Receives final (total, total) on
            completion. May be throttled by the caller.

    Returns:
        True if separation succeeded, False on error.
    """
    from demucs.apply import TensorChunk

    try:
        bag_model = _get_model()
        device = _get_device()

        logging.info("Demucs: loading audio...")
        audio, sr = sf.read(input_wav, dtype="float32")
        if audio.ndim == 1:
            wav = torch.from_numpy(np.stack([audio, audio]))
        else:
            wav = torch.from_numpy(audio.T)

        inner_models = bag_model.models if hasattr(bag_model, "models") else [bag_model]
        model_weights_list = (
            bag_model.weights
            if hasattr(bag_model, "weights")
            else [[1.0] * len(inner_models[0].sources)]
        )
        model_sr = inner_models[0].samplerate
        source_names = inner_models[0].sources

        if sr != model_sr:
            import librosa

            logging.info(f"Demucs: resampling {sr} -> {model_sr}")
            wav = torch.from_numpy(librosa.resample(wav.numpy(), orig_sr=sr, target_sr=model_sr))
            sr = model_sr

        if wav.shape[0] > 2:
            wav = wav[:2]

        ref = wav.mean(0)
        mean = ref.mean()
        std = ref.std()
        wav_norm = (wav - mean) / (std + 1e-8)

        channels = wav.shape[0]
        length = wav.shape[1]

        segment = inner_models[0].segment
        overlap = 0.25
        transition_power = 1.0
        segment_length = int(sr * segment)
        stride = int((1 - overlap) * segment_length)
        offsets = list(range(0, length, stride))

        weight = torch.cat(
            [
                torch.arange(1, segment_length // 2 + 1, device="cpu"),
                torch.arange(segment_length - segment_length // 2, 0, -1, device="cpu"),
            ]
        )
        weight = (weight / weight.max()) ** transition_power

        out = torch.zeros(len(source_names), channels, length)
        sum_weight = torch.zeros(length)

        vocals_idx = source_names.index("vocals")

        # Open both output files
        f_vocals = open(output_vocals, "wb")
        f_instrumental = open(output_instrumental, "wb")
        _write_wav_header(f_vocals, sr, channels, length)
        _write_wav_header(f_instrumental, sr, channels, length)

        written_up_to = 0
        segments_written = 0
        min_ready_segments = 2

        scale = float(format(stride / sr, ".2f"))
        progress_bar = tqdm(total=len(offsets), unit_scale=scale, ncols=120, unit="seconds")

        logging.info("Demucs: streaming separation started")

        for offset in offsets:
            chunk = TensorChunk(wav_norm[None], offset, segment_length)

            chunk_len_actual = min(segment_length, length - offset)
            chunk_out_acc = torch.zeros(1, len(source_names), channels, chunk_len_actual)
            totals = [0.0] * len(source_names)

            for sub_model, model_w in zip(inner_models, model_weights_list):
                original_device = next(iter(sub_model.parameters())).device
                sub_model.to(device)
                sub_model.eval()

                chunk_tensor = chunk.padded(int(segment * sr)).to(device)
                with torch.no_grad():
                    chunk_result = sub_model(chunk_tensor)
                chunk_result = chunk_result[..., :chunk_len_actual].cpu()
                sub_model.to(original_device)

                for k, iw in enumerate(model_w):
                    chunk_out_acc[:, k] += chunk_result[:, k] * iw
                    totals[k] += iw

            for k in range(len(source_names)):
                chunk_out_acc[:, k] /= totals[k]

            chunk_out = chunk_out_acc[0]
            chunk_len = chunk_out.shape[-1]

            out[..., offset : offset + chunk_len] += weight[:chunk_len] * chunk_out
            sum_weight[offset : offset + chunk_len] += weight[:chunk_len]

            safe_up_to = (
                min(offset + stride, length) if offset + segment_length < length else length
            )

            if safe_up_to > written_up_to and sum_weight[written_up_to:safe_up_to].min() > 0:
                finalized = (
                    out[..., written_up_to:safe_up_to] / sum_weight[written_up_to:safe_up_to]
                )
                finalized = finalized * std + mean

                # Vocals
                vocal_data = finalized[vocals_idx].numpy().T
                vocal_int16 = np.clip(vocal_data * 32767, -32768, 32767).astype(np.int16)
                f_vocals.write(vocal_int16.tobytes())
                f_vocals.flush()

                # Instrumental (sum of all non-vocal stems)
                instrumental_data = sum(
                    finalized[i].numpy() for i in range(len(source_names)) if i != vocals_idx
                ).T
                instrumental_int16 = np.clip(instrumental_data * 32767, -32768, 32767).astype(
                    np.int16
                )
                f_instrumental.write(instrumental_int16.tobytes())
                f_instrumental.flush()

                written_up_to = safe_up_to
                segments_written += 1

                if segments_written == min_ready_segments and ready_event:
                    ready_event.set()
                    logging.info(
                        f"Demucs: {segments_written} segments ready ({written_up_to / sr:.1f}s)"
                    )

                if progress_callback:
                    try:
                        progress_callback(written_up_to / sr, length / sr)
                    except Exception:
                        logging.exception("Demucs progress_callback failed")

            progress_bar.update(1)

        progress_bar.close()

        # Write remaining samples
        if written_up_to < length:
            remaining = out[..., written_up_to:length] / sum_weight[written_up_to:length].clamp(
                min=1e-8
            )
            remaining = remaining * std + mean

            vocal_rem = remaining[vocals_idx].numpy().T
            vocal_int16 = np.clip(vocal_rem * 32767, -32768, 32767).astype(np.int16)
            f_vocals.write(vocal_int16.tobytes())
            f_vocals.flush()

            instrumental_rem = sum(
                remaining[i].numpy() for i in range(len(source_names)) if i != vocals_idx
            ).T
            instrumental_int16 = np.clip(instrumental_rem * 32767, -32768, 32767).astype(np.int16)
            f_instrumental.write(instrumental_int16.tobytes())
            f_instrumental.flush()

        f_vocals.close()
        f_instrumental.close()
        # Short files (fewer than min_ready_segments) never hit the in-loop
        # set; fire here so the frontend never hangs waiting.
        if ready_event and not ready_event.is_set():
            ready_event.set()
            logging.info("Demucs: end of stream, stems ready")
        logging.info("Demucs: separation complete")
        if progress_callback:
            try:
                progress_callback(length / sr, length / sr)
            except Exception:
                logging.exception("Demucs progress_callback failed")
        return True

    except Exception:
        logging.exception("Demucs separation failed")
        if ready_event:
            ready_event.set()
        return False


# --- Per-song separation coordination ---
#
# Three entry points can race to separate the same song: download_manager's
# post-download prewarm (on the .m4a), lyrics' whisperx prewarm (on the .mp4),
# and stream_manager._prepare_stems at playback. Each would hit the same
# cache_key and write to the same .partial paths — a race on disk plus
# wasted CPU. The coordinator here forces a single owner per song, keyed by
# the resolved audio source (so `.m4a` and `.mp4` for the same song dedupe).


@dataclass
class SeparationHandle:
    """Shared state between the owner thread and any non-owner waiters.

    The owner sets ``ready_event`` as part of ``separate_stems`` (after the
    first 2 segments land on disk) and sets ``done_event`` via
    ``release_separation`` once finalize completes. Non-owners can wait on
    either to avoid duplicating work.
    """

    ready_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    success: bool = False


_sep_lock = threading.Lock()
_sep_handles: dict[str, SeparationHandle] = {}
_sep_done_keys: set[str] = set()


def acquire_separation(audio_source: str) -> tuple[bool, SeparationHandle]:
    """Atomically claim separation ownership for `audio_source`.

    Returns (is_owner, handle). When ``is_owner`` is True, the caller must
    run the separation and call ``release_separation(audio_source, success)``
    exactly once. When False, another caller is already separating (or has
    finished); the returned handle's events may be waited on.
    """
    with _sep_lock:
        if audio_source in _sep_done_keys:
            done = SeparationHandle(success=True)
            done.ready_event.set()
            done.done_event.set()
            return False, done
        existing = _sep_handles.get(audio_source)
        if existing is not None:
            return False, existing
        handle = SeparationHandle()
        _sep_handles[audio_source] = handle
        return True, handle


def release_separation(audio_source: str, success: bool) -> None:
    """Unblock any waiters and, on success, mark this song's cache as ready.

    Must be called exactly once by the owner of a prior ``acquire_separation``
    (even on failure, so waiters don't hang).
    """
    with _sep_lock:
        handle = _sep_handles.pop(audio_source, None)
        if success:
            _sep_done_keys.add(audio_source)
    if handle is not None:
        handle.success = success
        handle.ready_event.set()
        handle.done_event.set()


def resolve_audio_source(media_path: str) -> str:
    """Return the audio path to hash and feed into ffmpeg.

    When a sibling ``<basename>.m4a`` exists next to a video file, prefer
    it: playing from the audio-only stream makes both the cache key and
    the ffmpeg extract step stable and fast (no video demux). When the
    input is already an audio file, return it unchanged.
    """
    base, ext = os.path.splitext(media_path)
    if ext.lower() in (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus"):
        return media_path
    sibling = f"{base}.m4a"
    if os.path.isfile(sibling):
        return sibling
    return media_path


def prewarm(file_path: str) -> None:
    """Fire-and-forget: populate the Demucs cache for file_path.

    Idempotent across all entry points (download_manager, lyrics, main run
    loop). Deduplicates by resolved audio source so ``.mp4`` and sibling
    ``.m4a`` paths for the same song collapse to a single separation.
    """
    audio_source = resolve_audio_source(file_path)
    is_owner, _handle = acquire_separation(audio_source)
    if not is_owner:
        return  # already cached, or another caller is separating

    def _run() -> None:
        success = False
        try:
            cache_key = get_cache_key(audio_source)
            if get_cached_stems(cache_key):
                success = True
                return
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                input_wav = tmp.name
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        audio_source,
                        "-f",
                        "wav",
                        "-ar",
                        "44100",
                        input_wav,
                    ],
                    capture_output=True,
                    check=True,
                )
                partial_v, partial_i = partial_stem_paths(cache_key)
                if separate_stems(input_wav, partial_v, partial_i, _handle.ready_event):
                    finalize_partial_stems(cache_key)
                    encode_mp3_in_background(cache_key)
                    success = True
            finally:
                try:
                    os.remove(input_wav)
                except OSError:
                    pass
        except Exception:
            logging.exception(f"Demucs prewarm failed for {file_path}")
        finally:
            release_separation(audio_source, success)

    threading.Thread(target=_run, daemon=True).start()
