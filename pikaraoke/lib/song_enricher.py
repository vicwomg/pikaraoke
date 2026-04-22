"""Best-effort music metadata enrichment for downloaded songs.

Pipeline per song:
  1. Pick a query string from the ``songs`` row (register_download and
     the scanner both seed artist/title from yt-dlp's info.json);
     fall back to the filename stem when the row has no artist/title.
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

import logging
import os
import re
from datetime import datetime, timezone

import requests

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.lyrics import _VARIANT_RE
from pikaraoke.lib.music_metadata import fetch_itunes_track, fetch_musicbrainz_ids

logger = logging.getLogger(__name__)

COVER_ART_ROLE = "cover_art"
_COVER_DOWNLOAD_TIMEOUT_S = 5.0

_YT_ID_SUFFIX_RE = re.compile(r"(?:---[A-Za-z0-9_-]{11}|\s*\[[A-Za-z0-9_-]{11}\])$")


def _query_from_song(row, song_path: str) -> str:
    """Build an iTunes/MusicBrainz query: "<artist> - <title>".

    Prefers the ``songs`` row seeded by register_download / scanner; falls
    back to the filename stem (with the YouTube-id suffix stripped) when
    the row is missing artist or title. Empty string means "skip
    enrichment" — there's nothing to query with.
    """
    if row is not None:
        artist = (row["artist"] or "").strip()
        title = (row["title"] or "").strip()
        if artist and title:
            return f"{artist} - {title}"
    stem = os.path.splitext(os.path.basename(song_path))[0]
    return _YT_ID_SUFFIX_RE.sub("", stem).strip()


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


SOURCE_ITUNES = "itunes"
SOURCE_MUSICBRAINZ = "musicbrainz"


def _itunes_adds_variant(query: str, itunes: dict) -> bool:
    """True when iTunes' canonical track name adds a mix/version marker the
    original query did not have.

    Guards against iTunes' only hit being the instrumental/karaoke/live cut
    (common on small catalogues): overriding ``title`` with that suffix
    poisons every downstream LRCLib/Genius query, which index by the
    canonical song. Returns False when either side lacks a track name.
    """
    itunes_track = (itunes.get("track") or "").strip()
    if not itunes_track or not query:
        return False
    return bool(_VARIANT_RE.search(itunes_track)) and not _VARIANT_RE.search(query)


def enrich_song(db: KaraokeDatabase, song_id: int, song_path: str) -> None:
    """Run iTunes + MusicBrainz enrichment for a single song.

    Always updates ``metadata_status``, ``enrichment_attempts``, and
    ``last_enrichment_attempt`` so failed attempts are visible in the DB
    for later retry.

    Provenance (US-28): each metadata field is written via
    ``update_track_metadata_with_provenance`` with the originating source
    tag. The DB applies a confidence ladder (musicbrainz > itunes >
    youtube > scanner) so MusicBrainz-supplied artist/title overrides
    iTunes if both arrive, but neither overrides a ``manual`` write.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = db.get_song_by_id(song_id)
    if row is None:
        return

    query = _query_from_song(row, song_path)
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

    if _itunes_adds_variant(query, itunes):
        logger.info(
            "iTunes canonical track %r adds a variant marker not in %r; "
            "dropping title/artist override",
            itunes.get("track"),
            query,
        )
        itunes = {**itunes, "track": None, "artist": None}

    applied = db.update_track_metadata_with_provenance(
        song_id,
        SOURCE_ITUNES,
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
            applied.update(
                db.update_track_metadata_with_provenance(
                    song_id,
                    SOURCE_MUSICBRAINZ,
                    {
                        "musicbrainz_recording_id": mb.get("musicbrainz_recording_id"),
                        "isrc": mb.get("isrc"),
                    },
                )
            )

    cover_url = itunes.get("cover_art_url")
    if cover_url:
        cover_path = f"{os.path.splitext(song_path)[0]}.cover.jpg"
        if not os.path.exists(cover_path) and _download_cover(cover_url, cover_path):
            db.upsert_artifacts(song_id, [{"role": COVER_ART_ROLE, "path": cover_path}])

    db.stamp_enrichment_attempt(song_id, "enriched" if applied else "no_new_fields", now)
