"""Metadata providers for song enrichment.

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
    normalize_for_comparison,
    regex_tidy,
    remove_accents,
)

# iTunes nominally allows ~20 requests/minute.  A 2s floor between requests
# keeps us under the limit (HTTP round-trip adds ~0.5-1s on top) while the
# retry/backoff logic handles occasional 429s gracefully.
ITUNES_RATE_LIMIT = 2.0
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_MAX_RETRIES = 3
ITUNES_BACKOFF_BASE = 2.0
_RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}

_FEATURING_PATTERN = re.compile(
    r"\s+(?:ft\.?|feat\.?|featuring)\s+(?P<name>.*?)(?=\s+-\s+|$)", re.IGNORECASE
)

# Strip square-bracket qualifiers from iTunes titles ([Deluxe Edition], [Remastered], etc.)
_BRACKET_CONTENT_RE = re.compile(r"\s*\[[^\]]*\]")

# Pre-compiled patterns used in _suggestion_score
_PAREN_CONTENT_RE = re.compile(r"\s*(?:\([^)]*\)|\[[^\]]*\])")
_PAREN_EXTRACT_RE = re.compile(r"(?:\(([^)]*)\)|\[([^\]]*)\])")

_LEADING_CONJUNCTION_RE = re.compile(r"^(?:and|with)\s+", re.IGNORECASE)

# Matches 3+ single letters separated by spaces (e.g. "d i v o r c e" from "D.I.V.O.R.C.E.")
_SINGLE_LETTER_SEQ_RE = re.compile(r"\b([a-z](?:\s[a-z]){2,})\b")

_WHITESPACE_RE = re.compile(r"\s+")


def _fix_ssl_recursion() -> None:
    """Fix ssl.SSLContext.minimum_version RecursionError.

    On some Python/macOS/OpenSSL combinations, ssl.SSLContext.minimum_version's
    setter can infinitely recurse (observed on Python 3.11 and 3.13). We wrap
    urllib3's create_urllib3_context to catch the RecursionError and return a
    safe default context instead. On unaffected environments this is a no-op.
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

    def __init__(self, country: str = "US") -> None:
        self.country = country

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
        title = _BRACKET_CONTENT_RE.sub("", item.get("trackName", "")).strip()
        return {
            "artist": item.get("artistName", ""),
            "title": title,
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
                    params={
                        "term": query,
                        "media": "music",
                        "entity": "song",
                        "limit": limit,
                        "country": self.country,
                    },
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


def get_provider(preferences, country: str | None = None) -> MetadataProvider:
    """Resolve the active metadata provider from admin preferences.

    Args:
        country: Optional country override (e.g. from the edit page dropdown).
            When None, falls back to the saved preference.
    """
    provider_name = preferences.get("metadata_provider", "itunes")
    if provider_name != "itunes":
        logging.warning("Unknown metadata provider '%s', falling back to iTunes", provider_name)
    if country is None:
        country = preferences.get("itunes_search_country", "US")
    return ITunesProvider(country=country)


def _collapse_single_letters(match: re.Match) -> str:
    """Collapse 'd i v o r c e' into 'divorce'."""
    return match.group(1).replace(" ", "")


def _normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy matching: punctuation, case, and conjunctions.

    Extends normalize_for_comparison (which handles punctuation and case) with:
    - Comma stripping: 'Commodores, The' matches 'The Commodores'
    - Dotted-letter collapse: 'D.I.V.O.R.C.E.' -> 'divorce', 'S.O.S.' -> 'sos'
    - Conjunction normalization: 'Simon And Garfunkel' matches 'Simon & Garfunkel'
    """
    normalized = remove_accents(normalize_for_comparison(text))
    normalized = normalized.replace(",", "")
    # Collapse single-letter sequences separated by spaces (from dotted acronyms
    # like "D.I.V.O.R.C.E." which normalize_for_comparison turns into "d i v o r c e")
    normalized = _SINGLE_LETTER_SEQ_RE.sub(_collapse_single_letters, normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def _words_near_match(w1: str, w2: str) -> bool:
    """Check if two words differ by at most 1 edit (substitution, insertion, or deletion)."""
    if len(w1) < 3 or len(w2) < 3:
        return False
    if abs(len(w1) - len(w2)) > 1:
        return False
    if len(w1) == len(w2):
        return sum(c1 != c2 for c1, c2 in zip(w1, w2)) <= 1
    shorter, longer = (w1, w2) if len(w1) < len(w2) else (w2, w1)
    diffs = 0
    si = 0
    for li in range(len(longer)):
        if si < len(shorter) and shorter[si] == longer[li]:
            si += 1
        else:
            diffs += 1
    return diffs <= 1


def _fuzzy_match(a: str, b: str) -> bool:
    """Check if two normalized strings match: exact, substring, or high word overlap.

    Word overlap catches typos in multi-word names like 'Kayne' vs 'Kanye' where
    substring matching fails but most words still match.
    """
    if a == b or a in b or b in a:
        return True
    # Spaceless comparison: "acdc" matches "ac dc" (handles AC/DC vs ACDC, etc.)
    a_compact, b_compact = a.replace(" ", ""), b.replace(" ", "")
    if a_compact == b_compact or a_compact in b_compact or b_compact in a_compact:
        return True
    # Significant-word overlap, allowing near-matches for typos
    words_a = [w for w in a.split() if len(w) > 2]
    words_b = [w for w in b.split() if len(w) > 2]
    if not words_a or not words_b:
        return False
    matched = 0
    for wa in words_a:
        if any(wa == wb or _words_near_match(wa, wb) for wb in words_b):
            matched += 1
    smaller = min(len(words_a), len(words_b))
    return matched >= max(2, smaller - 1)


def _matches_field(part_norm: str, field_norm: str) -> bool:
    """Check if a normalized query part matches a normalized field value."""
    return part_norm == field_norm or _fuzzy_match(part_norm, field_norm)


def _normalize_query_parts(query: str) -> list[tuple[str, str]]:
    """Split and normalize a query into (raw, normalized) pairs."""
    return [
        (p.strip(), _normalize_for_matching(p.strip()))
        for p in query.lower().split(" - ", 1)
        if p.strip()
    ]


def _suggestion_score(
    result: dict,
    query: str,
    featuring: str = "",
) -> int:
    """Score a suggestion result for relevance, version quality, and genre."""
    score = 0
    title_lower = result.get("title", "").lower()
    artist_lower = result.get("artist", "").lower()
    # Strip parenthetical qualifiers so "Fernando (Live)" matches as "Fernando"
    title_base = _PAREN_CONTENT_RE.sub("", title_lower).strip()

    # Normalize conjunctions for matching ("and"/"&"/etc.)
    artist_norm = _normalize_for_matching(artist_lower)
    title_norm = _normalize_for_matching(title_base)
    title_full_norm = _normalize_for_matching(title_lower)

    query_parts_norm = _normalize_query_parts(query)
    featuring_norm = _normalize_for_matching(featuring) if featuring else ""

    artist_matched = False
    title_matched = False
    for part, part_norm in query_parts_norm:
        matched_artist = _matches_field(part_norm, artist_norm)
        matched_title = (
            part == title_lower or part == title_base or _matches_field(part_norm, title_norm)
        )
        artist_matched |= matched_artist
        title_matched |= matched_title
        exact_match = part_norm == artist_norm or part == title_lower or part == title_base
        fuzzy = _fuzzy_match(part_norm, artist_norm) or _fuzzy_match(part_norm, title_norm)
        substring = part_norm in artist_norm or part_norm in title_norm
        if exact_match:
            score += 50
        elif fuzzy:
            score += 40
        elif substring:
            score += 25

    # Cross-field bonus: when one query part matched the artist and another
    # matched the title, the result aligns with the query's structure.
    # Without this, a result where both parts coincidentally appear in the
    # title (e.g. a track titled "Dolly Parton + Beer Cereal Divorce" by
    # David Liebe Hart) could outscore a result with the correct artist.
    if len(query_parts_norm) >= 2 and artist_matched and title_matched:
        score += 30

    # Single-part decomposition: when the query has no separator but can be
    # split into artist + title, score each confirmed field independently.
    if len(query_parts_norm) == 1 and artist_matched and title_matched:
        part_norm = query_parts_norm[0][1]
        # Strip artist -> does remainder match title?
        artist_remainder = part_norm.replace(artist_norm, "", 1).strip()
        if artist_remainder and _matches_field(artist_remainder, title_norm):
            score += 40
        # Strip title -> does remainder match artist?
        title_remainder = part_norm.replace(title_norm, "", 1).strip()
        if title_remainder and _matches_field(title_remainder, artist_norm):
            score += 40

    # Bonus if the featuring artist appears in the result title
    # Normalize both sides so "and" matches "&" and accents are ignored
    if featuring_norm and featuring_norm in title_full_norm:
        score += 15

    # Bonus when query artist part has extra names that appear in the result's
    # parenthetical (e.g. query "Taylor Swift and Ed Sheeran" matches
    # title "Everything Has Changed (feat. Ed Sheeran)").
    # This handles "and"/"&"/"with" collaborator names without treating them
    # as featuring keywords (which would break genuine duos).
    if len(query_parts_norm) >= 2 and title_base != title_lower:
        query_artist_norm = query_parts_norm[0][1]
        parens_text = _extract_qualifier_text(title_lower)
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


def _detect_query_artist_first(query: str, top_result: dict) -> bool:
    """Detect if the query uses 'Artist - Title' order by checking the top result."""
    if " - " not in query:
        return False
    first_part = _normalize_for_matching(query.split(" - ", 1)[0])
    artist = _normalize_for_matching(top_result.get("artist", ""))
    return _fuzzy_match(first_part, artist)


def _extract_qualifier_text(text: str) -> str:
    """Extract all parenthetical and bracketed content as a single string."""
    return " ".join(g for groups in _PAREN_EXTRACT_RE.findall(text) for g in groups if g)


def suggest_metadata(
    display_name: str,
    provider: MetadataProvider | None = None,
    limit: int = 5,
) -> list[dict]:
    """Tidy a display name and search for metadata suggestions."""
    if provider is None:
        provider = ITunesProvider()
    tidied = regex_tidy(display_name)
    # Strip "ft"/"feat" -- clutters search and scoring,
    # but extract the featuring artist name for a bonus signal
    feat_match = _FEATURING_PATTERN.search(tidied)
    featuring = feat_match.group("name").strip() if feat_match else ""
    search_query = _FEATURING_PATTERN.sub("", tidied).strip()
    results = provider.search(search_query, limit=limit * _OVERFETCH_FACTOR)
    scored = _deduplicate_suggestions(results, search_query, featuring)
    truncated = scored[:limit]

    if not truncated:
        return []

    # Build output dicts with display and score fields (don't mutate originals)
    artist_first = _detect_query_artist_first(search_query, truncated[0][1])
    out = []
    for score, r in truncated:
        display = (
            f"{r['artist']} - {r['title']}" if artist_first else f"{r['title']} - {r['artist']}"
        )
        out.append({**r, "display": display, "score": score})
    return out
