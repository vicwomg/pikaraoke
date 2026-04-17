"""Resolve clean music metadata (artist, track) from noisy YouTube titles.

Used in two places:

  1. ``pikaraoke.lib.download_manager`` runs a lookup in parallel with yt-dlp
     and merges the result into ``<stem>.info.json`` before ``LyricsService``
     consumes it, so LRCLib receives canonical search terms.

  2. ``pikaraoke.lib.lyrics.LyricsService`` calls it as a fallback when the
     literal info.json fields return no LRCLib match.

Backed by the iTunes Search API - no auth, no documented rate limit, tolerant
fuzzy matching. Responses are memoized per-process; the cache is naturally
invalidated on restart.
"""

import logging
import re
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

_ITUNES_URL = "https://itunes.apple.com/search"
_TIMEOUT_S = 3.0

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


@lru_cache(maxsize=256)
def _search_itunes_cached(query: str, limit: int) -> tuple[tuple[str, str], ...]:
    """Hash-friendly cache wrapper. Returns a tuple of (artist, track) pairs."""
    if not query:
        return ()
    try:
        r = requests.get(
            _ITUNES_URL,
            params={"term": query, "entity": "song", "limit": limit},
            timeout=_TIMEOUT_S,
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
    pairs: list[tuple[str, str]] = []
    for item in results:
        artist = (item.get("artistName") or "").strip()
        track = (item.get("trackName") or "").strip()
        if artist and track:
            pairs.append((artist, track))
    return tuple(pairs)


def search_itunes(query: str, limit: int = 5) -> list[dict]:
    """Return iTunes song matches as ``[{"artist": ..., "track": ...}, ...]``."""
    return [{"artist": a, "track": t} for a, t in _search_itunes_cached(query, limit)]


def resolve_metadata(title: str) -> dict | None:
    """Resolve a noisy YouTube title to canonical ``{"artist", "track"}``.

    Returns None if iTunes has no match or the request failed.
    """
    query = _normalize_title(title)
    hits = search_itunes(query, limit=1)
    return hits[0] if hits else None
