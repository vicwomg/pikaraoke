"""Best-effort music metadata enrichment for downloaded songs.

Pipeline per song:
  1. Pick a query string (prefers the YouTube info.json track/artist pair
     when present, falls back to the title).
  2. Query iTunes for canonical artist/track + album + track_number +
     release_date + iTunes ID + cover-art URL.
  3. Query MusicBrainz with the iTunes-canonicalized artist/track for
     MusicBrainz recording ID + ISRC.
  4. Download cover art to ``<stem>.cover.jpg`` when a URL is available and
     register it as a ``cover_art`` artifact.
  5. Persist all populated fields via ``update_track_metadata`` and stamp
     ``metadata_status`` / ``enrichment_attempts`` / ``last_enrichment_attempt``.

All network calls are best-effort: failures are logged and swallowed so
enrichment cannot crash playback. The caller typically spawns this in a
background thread so the 3-6s of iTunes + MusicBrainz latency doesn't block
the download pipeline.
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.music_metadata import fetch_itunes_track, fetch_musicbrainz_ids

logger = logging.getLogger(__name__)

COVER_ART_ROLE = "cover_art"
_COVER_DOWNLOAD_TIMEOUT_S = 5.0


def _query_from_song(song_path: str) -> str:
    """Prefer info.json's explicit artist+track, else fall back to the filename."""
    info_path = f"{os.path.splitext(song_path)[0]}.info.json"
    if os.path.exists(info_path):
        try:
            with open(info_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
        artist = (data.get("artist") or "").strip()
        track = (data.get("track") or "").strip()
        if artist and track:
            return f"{artist} - {track}"
        title = (data.get("title") or "").strip()
        if title:
            return title
    stem = os.path.splitext(os.path.basename(song_path))[0]
    # Strip the 11-char YouTube id suffix in both "---ID" and "[ID]" forms.
    import re

    stem = re.sub(r"---[A-Za-z0-9_-]{11}$", "", stem)
    stem = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", stem)
    return stem.strip()


def _download_cover(url: str, dest: str) -> bool:
    """Download ``url`` to ``dest`` atomically. Returns True on success."""
    try:
        r = requests.get(url, timeout=_COVER_DOWNLOAD_TIMEOUT_S, stream=True)
    except requests.RequestException as e:
        logger.warning("cover download failed for %s: %s", url, e)
        return False
    if r.status_code != 200:
        logger.warning("cover HTTP %d for %s", r.status_code, url)
        return False
    tmp = dest + ".part"
    try:
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, dest)
    except OSError as e:
        logger.warning("cover write failed for %s: %s", dest, e)
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False
    return True


def enrich_song(db: KaraokeDatabase, song_id: int, song_path: str) -> None:
    """Run iTunes + MusicBrainz enrichment for a single song.

    Always updates ``metadata_status``, ``enrichment_attempts``, and
    ``last_enrichment_attempt`` so failed attempts are visible in the DB for
    later retry. Populated fields only overwrite NULLs — values the scanner
    or an earlier run already wrote are preserved.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = db.get_song_by_id(song_id)
    if row is None:
        return

    query = _query_from_song(song_path)
    if not query:
        db.stamp_enrichment_attempt(song_id, "skipped", now)
        return

    itunes = None
    try:
        itunes = fetch_itunes_track(query)
    except Exception:
        logger.exception("iTunes lookup crashed for %r", query)

    if not itunes:
        db.stamp_enrichment_attempt(song_id, "not_found", now)
        return

    # Only write fields that are currently NULL so we don't clobber manual edits
    # or earlier richer sources.
    updates = _nullable_updates(
        row,
        {
            "itunes_id": itunes.get("itunes_id"),
            "artist": itunes.get("artist"),
            "title": itunes.get("track"),
            "album": itunes.get("album"),
            "track_number": itunes.get("track_number"),
            "release_date": itunes.get("release_date"),
            "genre": itunes.get("genre"),
        },
    )

    # MusicBrainz is optional; skip when iTunes gave us no artist/track.
    mb_artist = itunes.get("artist")
    mb_track = itunes.get("track")
    if mb_artist and mb_track:
        try:
            mb = fetch_musicbrainz_ids(mb_artist, mb_track)
        except Exception:
            logger.exception("MusicBrainz lookup crashed for %r / %r", mb_artist, mb_track)
            mb = None
        if mb:
            updates.update(
                _nullable_updates(
                    row,
                    {
                        "musicbrainz_recording_id": mb.get("musicbrainz_recording_id"),
                        "isrc": mb.get("isrc"),
                    },
                )
            )

    if updates:
        db.update_track_metadata(song_id, **updates)

    cover_url = itunes.get("cover_art_url")
    if cover_url:
        cover_path = f"{os.path.splitext(song_path)[0]}.cover.jpg"
        if not os.path.exists(cover_path) and _download_cover(cover_url, cover_path):
            db.upsert_artifacts(song_id, [{"role": COVER_ART_ROLE, "path": cover_path}])

    db.stamp_enrichment_attempt(song_id, "enriched" if updates else "no_new_fields", now)


def _nullable_updates(row, new_values: dict) -> dict:
    """Return the subset of ``new_values`` whose DB column is currently NULL/empty.

    Preserves any value previously written (by the user, the scanner, or a
    richer source); iTunes is treated as a filler, never an override.
    """
    out = {}
    for key, value in new_values.items():
        if value is None or value == "":
            continue
        current = row[key] if key in row.keys() else None
        if current is None or current == "":
            out[key] = value
    return out
