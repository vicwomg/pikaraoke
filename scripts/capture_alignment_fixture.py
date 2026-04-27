"""Capture a single song's silero VAD onsets + LRClib LRC into the test fixtures dir.

Usage:
    python scripts/capture_alignment_fixture.py \\
        --audio "/path/to/song.m4a" \\
        --artist "Bonnie Tyler" \\
        --track "Total Eclipse of the Heart" \\
        --slug total_eclipse \\
        [--duration 333.95]

Writes:
    tests/fixtures/<slug>/vocal_onsets.json   # [(onset, next_onset), ...]
    tests/fixtures/<slug>/lyrics.lrc          # LRClib syncedLyrics

Requires the ``[align]`` extra (silero-vad). Network access for LRClib.

The integration tests load these fixtures directly and don't need
network or media files at run time, so capturing once + committing the
output is enough.
"""

import argparse
import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"

LRCLIB_URL = "https://lrclib.net/api/get"


def _vad_onsets(audio_path: str, sample_rate: int = 16000) -> tuple[list[tuple[float, float]], float]:
    """Capture vocal onsets using the same pipeline as ``vad_probe``.

    Calls ``vad_probe.list_vocal_onsets`` directly so the fixture
    reflects exactly what production sees. Probes audio duration via
    librosa for the JSON header.
    """
    import librosa

    from pikaraoke.lib import vad_probe

    pairs = vad_probe.list_vocal_onsets(audio_path)
    duration = float(librosa.get_duration(path=audio_path))
    rounded = [(round(o, 3), round(n, 3)) for o, n in pairs]
    return rounded, duration


def _lrclib_synced(artist: str, track: str, duration: float | None = None) -> str:
    params = {"artist_name": artist, "track_name": track}
    if duration is not None:
        params["duration"] = f"{duration:.0f}"
    resp = requests.get(LRCLIB_URL, params=params, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    synced = body.get("syncedLyrics") or ""
    if not synced.strip():
        raise SystemExit(f"LRClib returned no syncedLyrics for {artist} - {track}")
    return synced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, help="Path to the song's audio file (.m4a / .mp4)")
    parser.add_argument("--artist", required=True)
    parser.add_argument("--track", required=True)
    parser.add_argument("--slug", required=True, help="Fixture directory name under tests/fixtures/")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional song duration in seconds (passed to LRClib for disambiguation)",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"audio file not found: {audio_path}", file=sys.stderr)
        return 1

    onsets, duration = _vad_onsets(str(audio_path))
    lrc = _lrclib_synced(args.artist, args.track, duration=args.duration or duration)

    out_dir = FIXTURES_ROOT / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vocal_onsets.json").write_text(
        json.dumps(
            {
                "audio_duration_s": round(duration, 3),
                "onsets": [{"onset": o, "next_onset": n} for o, n in onsets],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "lyrics.lrc").write_text(lrc.strip() + "\n", encoding="utf-8")

    print(
        f"Wrote {out_dir} ({len(onsets)} VAD onsets, {len(lrc.splitlines())} LRC lines)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
