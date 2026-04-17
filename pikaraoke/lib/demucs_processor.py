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
import shutil
import struct
import threading

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

def get_cache_key(wav_path: str) -> str:
    """Compute SHA256 of decoded PCM WAV content. Metadata-invariant."""
    h = hashlib.sha256()
    with open(wav_path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)  # 1MB chunks
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def get_cached_stems(cache_key: str) -> tuple[str, str] | None:
    """Return (vocals_path, instrumental_path) if both exist in cache."""
    cache_path = os.path.join(CACHE_DIR, cache_key)
    vocals = os.path.join(cache_path, "vocals.wav")
    instrumental = os.path.join(cache_path, "instrumental.wav")
    if os.path.isfile(vocals) and os.path.isfile(instrumental):
        logging.info(f"Demucs cache hit: {cache_key[:12]}...")
        return vocals, instrumental
    return None


def cache_stems(cache_key: str, vocals_wav: str, instrumental_wav: str) -> None:
    """Copy completed WAVs to cache directory."""
    cache_path = os.path.join(CACHE_DIR, cache_key)
    os.makedirs(cache_path, exist_ok=True)
    shutil.copy2(vocals_wav, os.path.join(cache_path, "vocals.wav"))
    shutil.copy2(instrumental_wav, os.path.join(cache_path, "instrumental.wav"))
    logging.info(f"Demucs: cached stems at {cache_key[:12]}...")


# --- Separation ---

def separate_stems(
    input_wav: str,
    output_vocals: str,
    output_instrumental: str,
    ready_event: threading.Event | None = None,
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
        model_weights_list = bag_model.weights if hasattr(bag_model, "weights") else [[1.0] * len(inner_models[0].sources)]
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

        weight = torch.cat([
            torch.arange(1, segment_length // 2 + 1, device="cpu"),
            torch.arange(segment_length - segment_length // 2, 0, -1, device="cpu"),
        ])
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
        first_segment_written = False

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

            out[..., offset:offset + chunk_len] += weight[:chunk_len] * chunk_out
            sum_weight[offset:offset + chunk_len] += weight[:chunk_len]

            safe_up_to = min(offset + stride, length) if offset + segment_length < length else length

            if safe_up_to > written_up_to and sum_weight[written_up_to:safe_up_to].min() > 0:
                finalized = out[..., written_up_to:safe_up_to] / sum_weight[written_up_to:safe_up_to]
                finalized = finalized * std + mean

                # Vocals
                vocal_data = finalized[vocals_idx].numpy().T
                vocal_int16 = np.clip(vocal_data * 32767, -32768, 32767).astype(np.int16)
                f_vocals.write(vocal_int16.tobytes())
                f_vocals.flush()

                # Instrumental (sum of all non-vocal stems)
                instrumental_data = sum(finalized[i].numpy() for i in range(len(source_names)) if i != vocals_idx).T
                instrumental_int16 = np.clip(instrumental_data * 32767, -32768, 32767).astype(np.int16)
                f_instrumental.write(instrumental_int16.tobytes())
                f_instrumental.flush()

                written_up_to = safe_up_to

                if not first_segment_written:
                    first_segment_written = True
                    if ready_event:
                        ready_event.set()
                    logging.info(f"Demucs: first segment ready ({written_up_to / sr:.1f}s)")

            progress_bar.update(1)

        progress_bar.close()

        # Write remaining samples
        if written_up_to < length:
            remaining = out[..., written_up_to:length] / sum_weight[written_up_to:length].clamp(min=1e-8)
            remaining = remaining * std + mean

            vocal_rem = remaining[vocals_idx].numpy().T
            vocal_int16 = np.clip(vocal_rem * 32767, -32768, 32767).astype(np.int16)
            f_vocals.write(vocal_int16.tobytes())
            f_vocals.flush()

            instrumental_rem = sum(remaining[i].numpy() for i in range(len(source_names)) if i != vocals_idx).T
            instrumental_int16 = np.clip(instrumental_rem * 32767, -32768, 32767).astype(np.int16)
            f_instrumental.write(instrumental_int16.tobytes())
            f_instrumental.flush()

        f_vocals.close()
        f_instrumental.close()
        logging.info("Demucs: separation complete")
        return True

    except Exception:
        logging.exception("Demucs separation failed")
        if ready_event:
            ready_event.set()
        return False
