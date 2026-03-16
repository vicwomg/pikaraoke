"""Bulk iTunes metadata suggestion report.

Scans a song directory and runs each unique song stem through the same
suggest_metadata() pipeline used by the "Auto Suggest" button on the edit page.
Outputs a CSV and a console summary showing score distribution.

Usage:
    uv run python scripts/itunes_bulk_report.py SONGS_DIR
"""

import csv
import os
import sys
import time
from pathlib import Path

# Add project root to path so we can import pikaraoke modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from pikaraoke.lib.metadata_parser import regex_tidy, youtube_id_suffix
from pikaraoke.lib.metadata_providers import ITunesProvider, suggest_metadata

VALID_EXTENSIONS = {".mp4", ".mp3", ".zip", ".mkv", ".avi", ".webm", ".mov"}
DEFAULT_SONGS_DIR = None


def collect_unique_songs(songs_dir: str) -> list[str]:
    """Get unique filenames from the songs directory tree, filtered to valid media."""
    seen: set[str] = set()
    filenames: list[str] = []
    for _, _, entries in os.walk(songs_dir):
        for entry in sorted(entries):
            ext = os.path.splitext(entry)[1].lower()
            if ext not in VALID_EXTENSIONS:
                continue
            stem = os.path.splitext(entry)[0]
            if stem not in seen:
                seen.add(stem)
                filenames.append(entry)
    return filenames


def run_report(songs_dir: str) -> None:
    filenames = collect_unique_songs(songs_dir)
    total = len(filenames)
    print(f"Found {total} unique songs in {songs_dir}")
    print(f"Estimated time: ~{total * 3 // 60} minutes\n")  # ~3s effective (2s limit + RTT)

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

        for i, filename in enumerate(filenames, 1):
            # Replicate edit page: filename_from_path(path, tidy=False)
            # Pass full filename so youtube_id_suffix handles dots correctly
            stem = os.path.splitext(filename)[0]
            suffix = youtube_id_suffix(filename)
            clean_stem = stem[: -len(suffix)] if suffix else stem
            tidied = regex_tidy(clean_stem)
            eta_min = (total - i) * 3 / 60  # ~3s effective per song

            try:
                suggestions = suggest_metadata(clean_stem, provider=provider, limit=5)
            except (requests.exceptions.RequestException, ValueError, KeyError) as e:
                print(f"  [{i}/{total}] ERROR: {stem} -> {e}")
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
                    f"  [{i}/{total}] {status} ({score:>4}) "
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
                print(f"  [{i}/{total}] NONE {stem[:50]:<50}  (ETA: {eta_min:.0f}m)")
                row = {"stem": stem, "score": 0}
                writer.writerow([stem, tidied, 0, "", "", "", "", ""])

            results.append(row)

    # Summary
    scores = [r["score"] for r in results if r.get("score") is not None]
    queried = len(scores)
    elapsed_total = time.time() - start_time

    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total unique songs scanned: {total}")
    print(f"Successfully queried:       {queried}")
    print(f"Errors:                     {total - queried}")
    print(f"Time elapsed:               {elapsed_total / 60:.1f} minutes")
    print()

    thresholds = [120, 100, 80, 50, 0]
    for thresh in thresholds:
        count = sum(1 for s in scores if s >= thresh)
        pct = count / queried * 100 if queried else 0
        print(f"  Score >= {thresh:>3}: {count:>4} / {queried}  ({pct:5.1f}%)")

    neg = sum(1 for s in scores if s < 0)
    print(f"  Score <   0: {neg:>4} / {queried}  ({neg / queried * 100:5.1f}%)")

    print(f"\nFull results saved to: {csv_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} SONGS_DIR")
        sys.exit(1)
    songs_dir = sys.argv[1]
    if not os.path.isdir(songs_dir):
        print(f"Error: directory not found: {songs_dir}")
        sys.exit(1)
    run_report(songs_dir)
