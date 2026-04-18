"""On-the-fly audio processing for the direct-mp4 playback path.

When the video is served byte-for-byte from source, audio transforms
(pitch shift, loudness normalize) are applied in a separate pipe rather
than folded into a full video re-encode. Output is uncompressed 16-bit
48 kHz stereo WAV so HTTP Range requests map cleanly to byte offsets
(192000 bytes/sec; seek via ``ffmpeg -ss``).
"""

from __future__ import annotations

import logging
import re
import struct
import subprocess
from dataclasses import dataclass

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # s16
BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE  # 192000
WAV_HEADER_SIZE = 44


@dataclass(frozen=True)
class AudioTrackConfig:
    """Declares how to pipe audio from a source file with optional transforms."""

    source_path: str
    duration_sec: float
    semitones: int = 0
    normalize: bool = False

    @property
    def has_transforms(self) -> bool:
        return self.semitones != 0 or self.normalize


def total_wav_size(duration_sec: float) -> int:
    """Total byte length of the virtual WAV file (header + PCM)."""
    return WAV_HEADER_SIZE + int(duration_sec * BYTES_PER_SEC)


def build_wav_header(pcm_data_size: int) -> bytes:
    """Fixed 44-byte canonical WAV/PCM header for our rate+channels."""
    riff_size = 36 + pcm_data_size
    byte_rate = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE
    block_align = CHANNELS * BYTES_PER_SAMPLE
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            1,
            CHANNELS,
            SAMPLE_RATE,
            byte_rate,
            block_align,
            BYTES_PER_SAMPLE * 8,
        )
        + b"data"
        + struct.pack("<I", pcm_data_size)
    )


def build_audio_filters(semitones: int, normalize: bool) -> str | None:
    """Compose the ffmpeg -af graph for the requested transforms.

    Returns None when no transform is needed; the caller can omit -af to
    let ffmpeg resample straight to the target rate.
    """
    filters: list[str] = []
    if semitones != 0:
        filters.append(f"rubberband=pitch={2 ** (semitones / 12)}")
    if normalize:
        filters.append("loudnorm=i=-16:tp=-1.5:lra=11")
    return ",".join(filters) if filters else None


def build_pcm_command(
    source_path: str, semitones: int, normalize: bool, start_sec: float
) -> list[str]:
    """ffmpeg argv that emits raw s16le PCM at SAMPLE_RATE/CHANNELS to stdout."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if start_sec > 0:
        # -ss before -i is fast (container-level seek) and accurate enough for
        # 150ms drift correction; re-decoding from zero on every range request
        # would add seconds to seek.
        cmd += ["-ss", f"{start_sec:.3f}"]
    cmd += ["-i", source_path, "-vn"]
    filters = build_audio_filters(semitones, normalize)
    if filters:
        cmd += ["-af", filters]
    cmd += [
        "-f",
        "s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-",
    ]
    return cmd


def parse_range(range_header: str | None, total: int) -> tuple[int, int]:
    """Return (start, end) inclusive for a ``Range: bytes=N-M`` header.

    Falls back to the whole file on missing/malformed headers or when the
    requested end exceeds the known total.
    """
    if not range_header:
        return 0, total - 1
    m = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not m:
        return 0, total - 1
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else total - 1
    end = min(end, total - 1)
    if start > end:
        return 0, total - 1
    return start, end


def stream_wav_range(config: AudioTrackConfig, range_header: str | None):
    """Yield the bytes [start, end] of the virtual WAV file for this config.

    Returns a tuple (generator, status_code, headers, total_size). The caller
    wraps the generator in a Flask Response.

    For requests that straddle the 44-byte header the header bytes come from
    ``build_wav_header``; PCM bytes come from an ffmpeg subprocess whose
    ``-ss`` is chosen to land one second at or before the requested PCM
    offset (we then discard the sub-second leading bytes for byte precision).
    """
    pcm_size = int(config.duration_sec * BYTES_PER_SEC)
    total = WAV_HEADER_SIZE + pcm_size
    start, end = parse_range(range_header, total)
    length = end - start + 1

    if range_header:
        status = 206
    else:
        status = 200

    headers = {
        "Content-Type": "audio/wav",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "no-cache, no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"

    def generate():
        remaining = length
        cursor = start

        if cursor < WAV_HEADER_SIZE and remaining > 0:
            header = build_wav_header(pcm_size)
            header_slice = header[cursor : min(cursor + remaining, WAV_HEADER_SIZE)]
            if header_slice:
                yield header_slice
                cursor += len(header_slice)
                remaining -= len(header_slice)

        if remaining <= 0:
            return

        pcm_start = cursor - WAV_HEADER_SIZE
        # Seek to an integer second at or before the target, then skip the
        # leading bytes for exact-byte Range fidelity. ffmpeg's container-level
        # -ss lands on a packet boundary; the skip covers the quantization.
        seek_sec = pcm_start // BYTES_PER_SEC
        skip = pcm_start - seek_sec * BYTES_PER_SEC

        cmd = build_pcm_command(
            config.source_path,
            config.semitones,
            config.normalize,
            start_sec=float(seek_sec),
        )
        logging.debug("Audio pipe: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert proc.stdout is not None
        try:
            while skip > 0:
                chunk = proc.stdout.read(min(skip, 64 * 1024))
                if not chunk:
                    return
                skip -= len(chunk)

            while remaining > 0:
                chunk = proc.stdout.read(min(remaining, 64 * 1024))
                if not chunk:
                    # Source shorter than declared duration — pad silence so
                    # the advertised Content-Length stays honest.
                    pad = b"\x00" * remaining
                    yield pad
                    return
                yield chunk
                remaining -= len(chunk)
        finally:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait(timeout=1)

    return generate, status, headers, total
