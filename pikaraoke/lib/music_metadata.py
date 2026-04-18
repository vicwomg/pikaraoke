"""Resolve clean music metadata (artist, track, album, IDs) from noisy YouTube titles.

Two providers, both best-effort:

  1. iTunes Search API (primary) — no auth, fuzzy-friendly, returns track ID,
     album, track number, release date, cover art URL. Used by
     ``download_manager`` and ``lyrics.LyricsService`` to canonicalize search
     terms and by the song enricher to populate ``songs.itunes_id`` etc.

  2. MusicBrainz (secondary) — used only by the enricher to fill
     ``musicbrainz_recording_id`` and ``isrc``, which iTunes does not expose.
     Rate-limited to ~1 req/s by MusicBrainz itself; we keep timeouts short
     and swallow failures.

Responses are memoized per-process; the cache naturally invalidates on restart.
"""

import logging
import re
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

_ITUNES_URL = "https://itunes.apple.com/search"
_ITUNES_TIMEOUT_S = 3.0

_MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/recording"
_MUSICBRAINZ_TIMEOUT_S = 3.0
_MUSICBRAINZ_USER_AGENT = "PiKaraoke/1.0 ( https://github.com/vicwomg/pikaraoke )"

_PARENS_RE = re.compile(r"\s*[\(\[][^()\[\]]*[\)\]]")
_TOPIC_SUFFIX_RE = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(raw: str) -> str:
    """Strip YouTube boilerplate so the query reads like a clean search term.

    Drops parenthesized/bracketed content ("Official Video", "Long Version",
    "Lyrics", etc.) and the YouTube Music " - Topic" channel suffix. Leaves
    "ft./feat. X" alone - iTunes matches it fuzzily and the canonical track
    name often includes it anyway.
    """
    text = _PARENS_RE.sub("", raw)
    # Parens can nest shallowly in practice; run once more to catch the outer pair.
    text = _PARENS_RE.sub("", text)
    text = _TOPIC_SUFFIX_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # Strip stray leading/trailing separators left by removed parens.
    return text.strip(" -\u2013\u2014")


# Fields extracted from iTunes per hit. Tuple shape is needed for the LRU cache
# key; the readers below project it back to dicts.
_ITUNES_FIELDS = (
    "artistName",
    "trackName",
    "trackId",
    "collectionName",
    "trackNumber",
    "releaseDate",
    "artworkUrl100",
    "primaryGenreName",
)


@lru_cache(maxsize=256)
def _search_itunes_cached(query: str, limit: int) -> tuple[tuple, ...]:
    """Hash-friendly cache wrapper. Returns a tuple of value-tuples (see _ITUNES_FIELDS)."""
    if not query:
        return ()
    try:
        r = requests.get(
            _ITUNES_URL,
            params={"term": query, "entity": "song", "limit": limit},
            timeout=_ITUNES_TIMEOUT_S,
        )
    except requests.RequestException as e:
        logger.warning("iTunes request failed for %r: %s", query, e)
        return ()
    if r.status_code != 200:
        logger.warning("iTunes HTTP %d for %r", r.status_code, query)
        return ()
    try:
        data = r.json()
    except ValueError as e:
        logger.warning("iTunes invalid JSON for %r: %s", query, e)
        return ()
    results = data.get("results") or []
    rows: list[tuple] = []
    for item in results:
        artist = (item.get("artistName") or "").strip()
        track = (item.get("trackName") or "").strip()
        if not artist or not track:
            continue
        rows.append(
            tuple(
                (item.get(field) or "").strip()
                if isinstance(item.get(field), str)
                else item.get(field)
                for field in _ITUNES_FIELDS
            )
        )
    return tuple(rows)


def _itunes_row_to_dict(row: tuple) -> dict:
    """Project an iTunes row tuple back to a dict keyed by iTunes field names."""
    return dict(zip(_ITUNES_FIELDS, row))


def search_itunes(query: str, limit: int = 5) -> list[dict]:
    """Return iTunes song matches as ``[{"artist": ..., "track": ...}, ...]``.

    Back-compat shape used by lyrics.py and download_manager.
    """
    return [
        {"artist": r["artistName"], "track": r["trackName"]}
        for r in (_itunes_row_to_dict(row) for row in _search_itunes_cached(query, limit))
    ]


def search_itunes_full(query: str, limit: int = 5) -> list[dict]:
    """Return iTunes matches with all extracted fields (used by the enricher)."""
    return [_itunes_row_to_dict(row) for row in _search_itunes_cached(query, limit)]


def resolve_metadata(title: str) -> dict | None:
    """Resolve a noisy YouTube title to canonical ``{"artist", "track"}``.

    Returns None if iTunes has no match or the request failed.
    """
    hits = search_itunes(_normalize_title(title), limit=1)
    return hits[0] if hits else None


def _upscale_artwork(url: str, target: int = 600) -> str:
    """Rewrite iTunes artwork URL to request a larger size.

    iTunes URLs end in ``100x100bb.jpg``; swapping the dimensions gets us
    up to 600x600 at the same CDN path. Unmodified on mismatch.
    """
    return re.sub(r"/\d+x\d+(bb)?\.(jpg|png|jpeg)$", f"/{target}x{target}bb.jpg", url)


def fetch_itunes_track(title: str) -> dict | None:
    """Top match from iTunes with the full extracted field set.

    Shape: ``{itunes_id, artist, track, album, track_number, release_date,
    cover_art_url, genre}`` — keys may be missing/empty when iTunes doesn't
    supply them. Returns None when iTunes returns no match.
    """
    hits = search_itunes_full(_normalize_title(title), limit=1)
    if not hits:
        return None
    h = hits[0]
    artwork = h.get("artworkUrl100") or ""
    return {
        "itunes_id": str(h["trackId"]) if h.get("trackId") else None,
        "artist": h.get("artistName") or None,
        "track": h.get("trackName") or None,
        "album": h.get("collectionName") or None,
        "track_number": h.get("trackNumber") or None,
        "release_date": h.get("releaseDate") or None,
        "cover_art_url": _upscale_artwork(artwork) if artwork else None,
        "genre": h.get("primaryGenreName") or None,
    }


@lru_cache(maxsize=256)
def _search_musicbrainz_cached(artist: str, track: str) -> tuple[str, str] | None:
    """Query MusicBrainz for a recording; cache ``(mbid, isrc_or_empty)`` per (artist, track).

    Returns None when no match or on request failure. MusicBrainz is
    well-behaved about rate limits; we keep the timeout short and swallow
    transient errors so enrichment stays best-effort.
    """
    if not artist or not track:
        return None
    query = f'artist:"{artist}" AND recording:"{track}"'
    try:
        r = requests.get(
            _MUSICBRAINZ_URL,
            params={"query": query, "fmt": "json", "limit": 1},
            timeout=_MUSICBRAINZ_TIMEOUT_S,
            headers={"User-Agent": _MUSICBRAINZ_USER_AGENT},
        )
    except requests.RequestException as e:
        logger.warning("MusicBrainz request failed for %r / %r: %s", artist, track, e)
        return None
    if r.status_code != 200:
        logger.warning("MusicBrainz HTTP %d for %r / %r", r.status_code, artist, track)
        return None
    try:
        data = r.json()
    except ValueError as e:
        logger.warning("MusicBrainz invalid JSON: %s", e)
        return None
    recordings = data.get("recordings") or []
    if not recordings:
        return None
    rec = recordings[0]
    mbid = rec.get("id")
    if not mbid:
        return None
    isrcs = rec.get("isrcs") or []
    return (mbid, isrcs[0] if isrcs else "")


def fetch_musicbrainz_ids(artist: str, track: str) -> dict | None:
    """Return ``{musicbrainz_recording_id, isrc}`` or None.

    ``isrc`` is set to None when MusicBrainz returned a recording but no ISRC.
    """
    result = _search_musicbrainz_cached(artist, track)
    if result is None:
        return None
    mbid, isrc = result
    return {"musicbrainz_recording_id": mbid, "isrc": isrc or None}
