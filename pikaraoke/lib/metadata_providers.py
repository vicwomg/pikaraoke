"""Metadata providers for song enrichment: iTunes now, pluggable for Spotify later.

Pure library module -- no Flask imports.
"""

import logging
import re
import ssl
import time
from typing import Protocol

import requests
import urllib3.connection
import urllib3.util.ssl_

from pikaraoke.lib.metadata_parser import (
    SPECIAL_VERSION_KEYWORDS,
    _normalize_for_comparison,
    regex_tidy,
)

# iTunes rate limit: ~20 requests/minute (~3s per request)
ITUNES_RATE_LIMIT = 3.0
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_MAX_RETRIES = 3
ITUNES_BACKOFF_BASE = 2.0
_RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}


def _fix_ssl_recursion() -> None:
    """Fix CPython 3.13 ssl.SSLContext.minimum_version RecursionError.

    On Python 3.13, ssl.SSLContext.minimum_version's setter can infinitely
    recurse. gevent's monkey.patch_all() makes this worse by leaving urllib3
    with a reference to the unpatched (broken) ssl module. We wrap urllib3's
    create_urllib3_context to catch the RecursionError and return a safe
    default context instead.
    """
    original = urllib3.util.ssl_.create_urllib3_context

    def _safe_create_urllib3_context(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except RecursionError:
            return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    urllib3.util.ssl_.create_urllib3_context = _safe_create_urllib3_context
    urllib3.connection.create_urllib3_context = _safe_create_urllib3_context


_fix_ssl_recursion()


class MetadataProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search for tracks. Returns [{artist, title, year, genre, source}]."""
        ...

    def lookup(self, artist: str, title: str) -> dict | None:
        """Single best match for enrichment. Returns {artist, title, year, genre, source} or None."""
        ...


class ITunesProvider:
    """Search the iTunes Search API for track metadata."""

    _last_request_time = 0.0

    def _enforce_rate_limit(self) -> None:
        elapsed = time.time() - ITunesProvider._last_request_time
        if elapsed < ITUNES_RATE_LIMIT:
            time.sleep(ITUNES_RATE_LIMIT - elapsed)

    def _record_request(self) -> None:
        ITunesProvider._last_request_time = time.time()

    def _backoff(self, attempt: int, query: str, reason: str) -> None:
        """Sleep with exponential backoff before retrying."""
        delay = ITUNES_RATE_LIMIT + ITUNES_BACKOFF_BASE ** (attempt + 1)
        logging.info(
            "iTunes retry %d for query '%s' (%s), backing off %.1fs",
            attempt + 1,
            query,
            reason,
            delay,
        )
        time.sleep(delay)

    def _parse_result(self, item: dict) -> dict:
        release_date = item.get("releaseDate", "")
        year = release_date[:4] if len(release_date) >= 4 else ""
        return {
            "artist": item.get("artistName", ""),
            "title": item.get("trackName", ""),
            "year": year,
            "genre": item.get("primaryGenreName", ""),
            "source": "itunes",
        }

    def search(self, query: str, limit: int = 5, max_retries: int = 0) -> list[dict]:
        """Search iTunes for tracks matching a query.

        Args:
            max_retries: Number of retry attempts for transient failures (timeouts,
                rate limits, server errors). Use 0 for interactive contexts (edit page)
                and ITUNES_MAX_RETRIES for background enrichment.
        """
        for attempt in range(max_retries + 1):
            self._enforce_rate_limit()
            try:
                response = requests.get(
                    ITUNES_SEARCH_URL,
                    params={"term": query, "media": "music", "entity": "song", "limit": limit},
                    timeout=10,
                )
            except requests.exceptions.Timeout:
                logging.warning("iTunes API request timed out for query: %s", query)
                if attempt < max_retries:
                    self._backoff(attempt, query, "timeout")
                    continue
                return []
            except requests.exceptions.RequestException as e:
                logging.error("iTunes API request failed for query: %s: %s", query, e)
                return []
            finally:
                self._record_request()

            if response.status_code in _RETRYABLE_STATUS_CODES:
                logging.warning(
                    "iTunes API returned status %d for query: %s", response.status_code, query
                )
                if attempt < max_retries:
                    self._backoff(attempt, query, f"HTTP {response.status_code}")
                    continue
                return []

            if response.status_code != 200:
                logging.warning(
                    "iTunes API returned status %d for query: %s", response.status_code, query
                )
                return []

            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                logging.error("iTunes API returned invalid JSON for query: %s", query)
                return []

            results = data.get("results", [])
            return [
                self._parse_result(item) for item in results if item.get("wrapperType") == "track"
            ]

        return []

    def lookup(self, artist: str, title: str, max_retries: int = ITUNES_MAX_RETRIES) -> dict | None:
        """Single best match for a known artist/title pair.

        Defaults to ITUNES_MAX_RETRIES since lookup is typically called from
        background enrichment where latency is not a concern.
        """
        results = self.search(f"{artist} {title}", limit=5, max_retries=max_retries)
        if not results:
            return None

        artist_lower = artist.lower()
        # Prefer exact artist match
        for r in results:
            if r["artist"].lower() == artist_lower:
                return r
        return results[0]


def get_provider(preferences) -> MetadataProvider:
    """Resolve the active metadata provider from admin preferences.

    Currently only supports iTunes. The preference key and factory pattern
    exist so that Spotify (or other providers) can be added by extending
    the if/elif chain -- no pipeline or route changes needed.
    """
    provider_name = preferences.get("metadata_provider", "itunes")
    if provider_name != "itunes":
        logging.warning("Unknown metadata provider '%s', falling back to iTunes", provider_name)
    return ITunesProvider()


def _normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy matching: punctuation, case, and conjunctions.

    Extends _normalize_for_comparison (which handles punctuation and case)
    with conjunction normalization so 'Simon And Garfunkel' matches 'Simon & Garfunkel'.
    """
    normalized = _normalize_for_comparison(text)
    return normalized.replace(" & ", " and ")


def _fuzzy_match(a: str, b: str) -> bool:
    """Check if two normalized strings match: exact, substring, or high word overlap.

    Word overlap catches typos in multi-word names like 'Kayne' vs 'Kanye' where
    substring matching fails but most words still match.
    """
    if a == b or a in b or b in a:
        return True
    # Significant-word overlap (skip short conjunctions)
    words_a = {w for w in a.split() if len(w) > 2}
    words_b = {w for w in b.split() if len(w) > 2}
    if not words_a or not words_b:
        return False
    overlap = words_a & words_b
    smaller = min(len(words_a), len(words_b))
    return len(overlap) >= max(2, smaller - 1)


def _suggestion_score(result: dict, query: str, featuring: str = "") -> int:
    """Score a suggestion result for relevance, version quality, and genre."""
    score = 0
    title_lower = result.get("title", "").lower()
    artist_lower = result.get("artist", "").lower()
    # Strip parenthetical qualifiers so "Fernando (Live)" matches as "Fernando"
    title_base = _PAREN_CONTENT_RE.sub("", title_lower).strip()

    # Normalize conjunctions for matching ("and"/"&"/etc.)
    artist_norm = _normalize_for_matching(artist_lower)
    title_norm = _normalize_for_matching(title_base)

    # Query relevance: split "artist - title" and match against result fields
    parts = [p.strip() for p in query.lower().split(" - ", 1)]
    for part in parts:
        if not part:
            continue
        part_norm = _normalize_for_matching(part)
        if part_norm == artist_norm or part == title_lower or part == title_base:
            score += 50
        elif _fuzzy_match(part_norm, artist_norm) or _fuzzy_match(part_norm, title_norm):
            score += 40
        elif part_norm in artist_norm or part_norm in title_norm:
            score += 25

    # Bonus if the featuring artist appears in the result title
    if featuring and featuring.lower() in title_lower:
        score += 15

    # Bonus when query artist part has extra names that appear in the result's
    # parenthetical (e.g. query "Taylor Swift and Ed Sheeran" matches
    # title "Everything Has Changed (feat. Ed Sheeran)").
    # This handles "and"/"&"/"with" collaborator names without treating them
    # as featuring keywords (which would break genuine duos).
    if len(parts) >= 2 and title_base != title_lower:
        query_artist_norm = _normalize_for_matching(parts[0])
        parens_text = " ".join(_PAREN_EXTRACT_RE.findall(title_lower))
        if parens_text and artist_norm != query_artist_norm:
            # Extract the extra part of the query artist beyond the result artist
            extra = query_artist_norm.replace(artist_norm, "").strip()
            extra = _LEADING_CONJUNCTION_RE.sub("", extra).strip()
            if extra and len(extra) > 2 and extra in parens_text:
                score += 15

    # Penalize titles with parenthetical qualifiers (sessions, performances, etc.)
    if title_base != title_lower:
        score -= 10

    # Penalize special versions (live, remix, etc.)
    for keyword in SPECIAL_VERSION_KEYWORDS:
        if keyword in title_lower:
            score -= 30
            break
    if len(title_lower) > 60:
        score -= 20

    # Prefer simple genres ("Rock") over compound ones ("Singer/Songwriter")
    genre = result.get("genre", "")
    if "/" in genre or "&" in genre:
        score -= 5
    return score


def _deduplicate_suggestions(
    results: list[dict], query: str, featuring: str = ""
) -> list[tuple[int, dict]]:
    """Keep the highest-scored result per unique (artist, title) pair.

    Returns (score, result) tuples sorted by score descending.
    """
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    for r in results:
        key = (r.get("artist", "").lower(), r.get("title", "").lower())
        s = _suggestion_score(r, query, featuring)
        if key not in best or s > best[key][0]:
            best[key] = (s, r)
    scored = sorted(best.values(), key=lambda x: x[0], reverse=True)
    return scored


# Over-fetch factor to compensate for deduplication
_OVERFETCH_FACTOR = 3


def _detect_query_artist_first(query: str, results: list[dict]) -> bool:
    """Detect if the query uses 'Artist - Title' order by checking the first result."""
    if " - " not in query or not results:
        return False
    first_part = _normalize_for_matching(query.split(" - ", 1)[0])
    top = results[0]
    artist = _normalize_for_matching(top.get("artist", ""))
    return _fuzzy_match(first_part, artist)


_FEATURING_PATTERN = re.compile(
    r"\s+(?:ft\.?|feat\.?|featuring)\s+(?P<name>.*?)(?=\s+-\s+|$)", re.IGNORECASE
)

# Pre-compiled patterns used in _suggestion_score
_PAREN_CONTENT_RE = re.compile(r"\s*\([^)]*\)")
_PAREN_EXTRACT_RE = re.compile(r"\(([^)]*)\)")
_LEADING_CONJUNCTION_RE = re.compile(r"^(?:and|with)\s+", re.IGNORECASE)


def suggest_metadata(
    display_name: str,
    provider: MetadataProvider | None = None,
    limit: int = 5,
) -> list[dict]:
    """Tidy a display name and search for metadata suggestions."""
    if provider is None:
        provider = ITunesProvider()
    tidied = regex_tidy(display_name)
    # Strip "ft"/"feat" — clutters both iTunes search and scoring,
    # but extract the featuring artist name for a bonus signal
    feat_match = _FEATURING_PATTERN.search(tidied)
    featuring = feat_match.group("name").strip() if feat_match else ""
    search_query = _FEATURING_PATTERN.sub("", tidied).strip()
    results = provider.search(search_query, limit=limit * _OVERFETCH_FACTOR)
    scored = _deduplicate_suggestions(results, search_query, featuring)
    truncated = scored[:limit]

    # Add display and score fields using pre-computed scores
    out = [r for _, r in truncated]
    artist_first = _detect_query_artist_first(search_query, out)
    for score, r in truncated:
        if artist_first:
            r["display"] = f"{r['artist']} - {r['title']}"
        else:
            r["display"] = f"{r['title']} - {r['artist']}"
        r["score"] = score

    return out
