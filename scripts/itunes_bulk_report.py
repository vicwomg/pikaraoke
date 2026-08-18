"""Bulk iTunes metadata suggestion report.

Scans a song directory and runs each unique song stem through the same
suggest_metadata() pipeline used by the "Auto Suggest" button on the edit page.
Outputs a CSV and a console summary showing score distribution.

Usage:
    uv run python scripts/itunes_bulk_report.py SONGS_DIR [--country XX]
"""

import argparse
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


def run_report(songs_dir: str, country: str = "US") -> None:
    filenames = collect_unique_songs(songs_dir)
    total = len(filenames)
    print(f"Found {total} unique songs in {songs_dir}")
    print(f"iTunes country: {country}")
    print(f"Estimated time: ~{total * 3 // 60} minutes\n")  # ~3s effective (2s limit + RTT)

    provider = ITunesProvider(country=country)
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
                "reason",
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
                writer.writerow([stem, tidied, "ERROR", type(e).__name__, "", "", "", "", ""])
                results.append({"stem": stem, "score": None, "reason": type(e).__name__})
                continue

            if suggestions:
                top = suggestions[0]
                score = top.get("score", 0)
                row = {
                    "stem": stem,
                    "tidied": tidied,
                    "score": score,
                    "reason": top.get("reason", ""),
                    "display": top.get("display", ""),
                    "artist": top.get("artist", ""),
                    "title": top.get("title", ""),
                    "year": top.get("year", ""),
                    "genre": top.get("genre", ""),
                }
                status = "OK" if score >= 95 else "LOW"
                print(
                    f"  [{i}/{total}] {status} ({score:>4}) "
                    f"{stem[:44]:<44} -> {row['display'][:44]:<44}"
                    f" [{row['reason'][:24]:<24}] (ETA: {eta_min:.0f}m)"
                )
                writer.writerow(
                    [
                        stem,
                        tidied,
                        score,
                        row["reason"],
                        row["display"],
                        row["artist"],
                        row["title"],
                        row["year"],
                        row["genre"],
                    ]
                )
            else:
                print(f"  [{i}/{total}] NONE {stem[:50]:<50}  (ETA: {eta_min:.0f}m)")
                row = {"stem": stem, "score": 0, "reason": "no results"}
                writer.writerow([stem, tidied, 0, "no results", "", "", "", "", ""])

            results.append(row)

    # Summary
    scores = [r["score"] for r in results if r.get("score") is not None]
    queried = len(scores)
    elapsed_total = time.time() - start_time

    print(f"\n{'=' * 60}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total unique songs scanned: {total}")
    print(f"Successfully queried:       {queried}")
    print(f"Errors:                     {total - queried}")
    print(f"Time elapsed:               {elapsed_total / 60:.1f} minutes")
    print()

    # The four anchors, so the counts size the three groups directly: >= 100 is
    # already correct, 95..98 is what a bulk run would touch, the rest is review.
    for thresh in (100, 98, 95, 80, 0):
        count = sum(1 for s in scores if s >= thresh)
        pct = count / queried * 100 if queried else 0
        print(f"  Score >= {thresh:>3}: {count:>4} / {queried}  ({pct:5.1f}%)")

    # A count of songs below the band means nothing on its own. "iTunes had
    # nothing to offer" wants a better query; "we held it back" is the
    # threshold's own doing, and only that second group argues for moving it.
    for label, keep in (("below the band", False), ("inside it", True)):
        group = [r for r in results if r.get("score") is not None and ((r["score"] >= 95) == keep)]
        if not group:
            continue
        tally: dict[str, int] = {}
        for r in group:
            reason = r.get("reason") or "unknown"
            tally[reason] = tally.get(reason, 0) + 1
        print(f"\n  Why, for everything {label}:")
        for reason, count in sorted(tally.items(), key=lambda kv: -kv[1]):
            pct = count / queried * 100 if queried else 0
            print(f"    {reason:<26} {count:>4} / {queried}  ({pct:5.1f}%)")

    print(f"\nFull results saved to: {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk iTunes metadata suggestion report.")
    parser.add_argument("songs_dir", help="Directory containing song files")
    parser.add_argument(
        "--country",
        default="US",
        help="iTunes store country code (default: US)",
    )
    args = parser.parse_args()
    if not os.path.isdir(args.songs_dir):
        print(f"Error: directory not found: {args.songs_dir}")
        sys.exit(1)
    run_report(args.songs_dir, country=args.country)
