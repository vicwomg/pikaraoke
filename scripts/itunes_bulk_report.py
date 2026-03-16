"""Bulk iTunes metadata suggestion report.

Scans a song directory and runs each unique song stem through the same
suggest_metadata() pipeline used by the "Auto Suggest" button on the edit page.
Outputs a CSV and a console summary showing score distribution.

Usage:
    uv run python scripts/itunes_bulk_report.py [SONGS_DIR]
"""

import csv
import os
import sys
import time
from pathlib import Path

# Add project root to path so we can import pikaraoke modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pikaraoke.lib.metadata_parser import regex_tidy, youtube_id_suffix
from pikaraoke.lib.metadata_providers import ITunesProvider, suggest_metadata

VALID_EXTENSIONS = {".mp4", ".mp3", ".zip", ".mkv", ".avi", ".webm", ".mov"}
DEFAULT_SONGS_DIR = r"C:\Users\mannr\pikaraoke-songs"


def collect_unique_stems(songs_dir: str) -> list[str]:
    """Get unique file stems from the songs directory, filtered to valid media."""
    seen: set[str] = set()
    stems: list[str] = []
    for entry in sorted(os.listdir(songs_dir)):
        path = os.path.join(songs_dir, entry)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(entry)[1].lower()
        if ext not in VALID_EXTENSIONS:
            continue
        stem = os.path.splitext(entry)[0]
        if stem not in seen:
            seen.add(stem)
            stems.append(stem)
    return stems


def run_report(songs_dir: str) -> None:
    stems = collect_unique_stems(songs_dir)
    print(f"Found {len(stems)} unique songs in {songs_dir}")
    print(f"Estimated time: ~{len(stems) * 3 // 60} minutes\n")

    provider = ITunesProvider()
    csv_path = os.path.join(os.path.dirname(__file__), "itunes_report.csv")

    results: list[dict] = []
    start_time = time.time()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "original_stem",
                "tidied",
                "top_score",
                "suggested_display",
                "suggested_artist",
                "suggested_title",
                "year",
                "genre",
            ]
        )

        for i, stem in enumerate(stems, 1):
            # Strip YouTube ID suffix, matching what the edit page does
            suffix = youtube_id_suffix(stem)
            clean_stem = stem[: -len(suffix)] if suffix else stem
            tidied = regex_tidy(clean_stem)
            eta_min = (len(stems) - i) * 3 / 60

            try:
                suggestions = suggest_metadata(clean_stem, provider=provider, limit=5)
            except Exception as e:
                print(f"  [{i}/{len(stems)}] ERROR: {stem} -> {e}")
                writer.writerow([stem, tidied, "ERROR", "", "", "", "", ""])
                results.append({"stem": stem, "score": None})
                continue

            if suggestions:
                top = suggestions[0]
                score = top.get("score", 0)
                row = {
                    "stem": stem,
                    "tidied": tidied,
                    "score": score,
                    "display": top.get("display", ""),
                    "artist": top.get("artist", ""),
                    "title": top.get("title", ""),
                    "year": top.get("year", ""),
                    "genre": top.get("genre", ""),
                }
                status = "OK" if score >= 100 else "LOW"
                print(
                    f"  [{i}/{len(stems)}] {status} ({score:>4}) "
                    f"{stem[:50]:<50} -> {row['display'][:50]}"
                    f"  (ETA: {eta_min:.0f}m)"
                )
                writer.writerow(
                    [
                        stem,
                        tidied,
                        score,
                        row["display"],
                        row["artist"],
                        row["title"],
                        row["year"],
                        row["genre"],
                    ]
                )
            else:
                print(f"  [{i}/{len(stems)}] NONE {stem[:50]:<50}  (ETA: {eta_min:.0f}m)")
                row = {"stem": stem, "score": 0}
                writer.writerow([stem, tidied, 0, "", "", "", "", ""])

            results.append(row)

    # Summary
    scores = [r["score"] for r in results if r.get("score") is not None]
    total = len(scores)
    elapsed_total = time.time() - start_time

    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total unique songs scanned: {len(stems)}")
    print(f"Successfully queried:       {total}")
    print(f"Errors:                     {len(stems) - total}")
    print(f"Time elapsed:               {elapsed_total / 60:.1f} minutes")
    print()

    thresholds = [120, 100, 80, 50, 0]
    for thresh in thresholds:
        count = sum(1 for s in scores if s >= thresh)
        pct = count / total * 100 if total else 0
        print(f"  Score >= {thresh:>3}: {count:>4} / {total}  ({pct:5.1f}%)")

    neg = sum(1 for s in scores if s < 0)
    print(f"  Score <   0: {neg:>4} / {total}  ({neg / total * 100:5.1f}%)")

    print(f"\nFull results saved to: {csv_path}")


if __name__ == "__main__":
    songs_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SONGS_DIR
    if not os.path.isdir(songs_dir):
        print(f"Error: directory not found: {songs_dir}")
        sys.exit(1)
    run_report(songs_dir)
