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


def _print_distribution(label: str, rows: list[dict], queried: int) -> None:
    """Score distribution and reasons for one provenance group."""
    scores = [r["score"] for r in rows if r.get("score") is not None]
    if not scores:
        return
    n = len(scores)
    print(f"\n  {label}: {n} songs, {n / queried * 100:.0f}% of those queried")
    # The three anchors, so the counts size the groups directly: >= 100 is
    # already correct, 95..98 is what a bulk run would touch.
    for thresh in (100, 98, 95):
        count = sum(1 for s in scores if s >= thresh)
        print(f"      score >= {thresh:>3}: {count:>4} / {n}  ({count / n * 100:5.1f}%)")
    # A count below the band means nothing on its own. "iTunes had nothing to
    # offer" wants a better query; "we held it back" is the threshold's own
    # doing, and only that second group argues for moving it.
    for sub, keep in (("inside the band", True), ("below it", False)):
        members = [r for r in rows if r.get("score") is not None and (r["score"] >= 95) == keep]
        if not members:
            continue
        tally: dict[str, int] = {}
        for r in members:
            reason = r.get("reason") or "unknown"
            tally[reason] = tally.get(reason, 0) + 1
        print(f"      why {sub}:")
        for reason, count in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"        {reason:<26} {count:>4} / {n}  ({count / n * 100:5.1f}%)")


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
                "youtube",
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
            yt = bool(suffix)
            tidied = regex_tidy(clean_stem)
            eta_min = (total - i) * 3 / 60  # ~3s effective per song

            try:
                suggestions = suggest_metadata(clean_stem, provider=provider, limit=5)
            except (requests.exceptions.RequestException, ValueError, KeyError) as e:
                print(f"  [{i}/{total}] ERROR: {stem} -> {e}")
                writer.writerow([stem, yt, tidied, "ERROR", type(e).__name__, "", "", "", "", ""])
                results.append(
                    {"stem": stem, "youtube": yt, "score": None, "reason": type(e).__name__}
                )
                continue

            if suggestions:
                top = suggestions[0]
                score = top.get("score", 0)
                row = {
                    "stem": stem,
                    "youtube": yt,
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
                        yt,
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
                row = {"stem": stem, "youtube": yt, "score": 0, "reason": "no results"}
                writer.writerow([stem, yt, tidied, 0, "no results", "", "", "", "", ""])

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

    # Auto-renaming YouTube downloads is what this feature exists for. A
    # library's other files arrive with their own naming conventions and are a
    # secondary concern, so one blended number hides how well the primary case
    # works -- and hides which group a disappointing number belongs to.
    for label, group in (
        ("YouTube-sourced", [r for r in results if r.get("youtube")]),
        ("Other provenance", [r for r in results if r.get("youtube") is False]),
    ):
        _print_distribution(label, group, queried)

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
