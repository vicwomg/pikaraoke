"""Song metadata parsing: Last.fm lookup, regex tidying, and search scoring.

Pure library module — no Flask imports. Extracted from batch_song_renamer.py
to enable reuse in the SQLite enrichment pipeline.
"""

import logging
import os
import re
import time
import unicodedata

import requests

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+"
)

NOISE_WORDS = [
    r"\bofficial\b",
    r"\bmusic\b",
    r"\bvideo\b",
    r"\baudio\b",
    r"\blyrics?\b",
    r"\bkaraoke\b",
    r"\bkaraokê\b",
    r"\bcomplete\b",
    r"\bcompleto\b",
    r"\bao vivo\b",
    r"\blive\b",
    r"\bremix\b",
    r"\bversion\b",
    r"\bfeat\.?\b",
    r"\bft\.?\b",
    r"\bremaster\b",
    r"\bas popularized by\b",
    r"\bkarafun\b",
    r"\binstrumental\b",
    r"\bminus one\b",
    r"\bminusone\b",
    r"\bmade famous by\b",
    r"\bby\b",
    r"\bkaraoke version\b",
    r"\bhd\b",
    r"\bhq\b",
    r"\bcoversph\b",
    r"\bsingkaraoke\b",
    r"\bin the style of\b",
    r"\bno lead vocal\b",
    r"\bwith lyrics\b",
    r"\bcc\b",
]
NOISE_PATTERN = re.compile("|".join(NOISE_WORDS), flags=re.IGNORECASE)

SPECIAL_VERSION_KEYWORDS = [
    " - ",
    "ao vivo",
    "live",
    "remix",
    "version",
    "karaoke",
    "acoustic",
    "instrumental",
    "cover",
    "radio edit",
    "extended",
]

LASTFM_API_KEY = "058c382f5fd686b4146f6028961c14da"
LASTFM_API_URL = "http://ws.audioscrobbler.com/2.0/"
# Last.fm rate limit: approximately 5 requests/second (conservative estimate)
LASTFM_RATE_LIMIT = 0.2  # seconds between requests (5 req/sec)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds: retries at 1s, 2s

# Sentinel distinguishing "rate limited" (transient) from "no results" (definitive)
_RATE_LIMITED = object()

_last_api_request_time = 0.0

# Attribution phrases that embed the artist name in karaoke filenames
ATTRIBUTION_PATTERNS = [
    # Parenthetical: "(Made Famous by Artist)", "(In the Style of Artist)", etc.
    re.compile(
        r"\((?:as\s+)?(?:made\s+famous\s+by|in\s+the\s+style\s+of"
        r"|originally\s+performed\s+by|as\s+popularized\s+by)[:\s]+([^)]+)\)",
        re.IGNORECASE,
    ),
    # Trailing inline: "Title made famous by Artist [noise]"
    re.compile(
        r"\b(?:made\s+famous\s+by|in\s+the\s+style\s+of"
        r"|originally\s+performed\s+by|as\s+popularized\s+by)[:\s]+(.+?)(?:\s*[\(\[]|$)",
        re.IGNORECASE,
    ),
]

TRAILING_NOISE_PATTERNS = [
    # "karaoke" at the trailing end means everything after it is noise
    # (source channels, version labels, etc.) — no need to enumerate them
    re.compile(r"\s*\bkaraoke\b.*$", re.IGNORECASE),
    re.compile(
        r"\s*\b(?:official\s+(?:music\s+)?video|lyrics?"
        r"|hd|hq|instrumental|with\s+lyrics|no\s+lead\s+vocal|cc)\b[\s.!]*$",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def _remove_accents(text: str) -> str:
    """Strip diacritical marks for accent-insensitive comparison."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _split_query_parts(query: str) -> tuple[str, str]:
    """Split a query like 'Artist - Title' into its two parts.

    Tries splitting with surrounding spaces first, then without.
    Returns (part1, part2) where part2 is empty if no separator found.
    """
    query_lower = query.lower()

    # Try to split by " - " or " | " (with spaces)
    parts = re.split(r"\s+[-\|\uff5c]\s+", query_lower)

    # Fall back to splitting without spaces around separator
    if len(parts) < 2:
        parts = re.split(r"\s*[-\|\uff5c]\s*", query_lower)

    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return query_lower.strip(), ""


# ---------------------------------------------------------------------------
# Search query cleaning
# ---------------------------------------------------------------------------


def clean_search_query(song_name: str) -> str:
    """Strip noise words, brackets, and emoji to isolate the core song identity."""
    song_name = EMOJI_PATTERN.sub("", song_name)
    song_name = song_name.replace("_", " ")
    song_name = re.sub(r"\([^)]*\)", "", song_name)
    song_name = re.sub(r"\[[^\]]*\]", "", song_name)

    song_name = NOISE_PATTERN.sub("", song_name)
    song_name = re.sub(r"\s+", " ", song_name.strip())

    return song_name


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _match_score(query_part: str, track_title: str) -> int:
    """Score how well a query part matches a track title."""
    if query_part == track_title:
        return 100
    if query_part in track_title:
        return 50
    return 0


def _word_match_score(query_part: str, track_title: str) -> int:
    """Score based on individual significant words matching."""
    has_match = any(word in track_title for word in query_part.split() if len(word) > 3)
    return 20 if has_match else 0


def score_result(result: dict, original_query: str) -> int:
    """Score a Last.fm result against the original query for relevance."""
    score = 0

    if result.get("name", "").isupper() or result.get("artist", "").isupper():
        score -= 10

    track_name = result.get("name", "").lower()
    artist_name = result.get("artist", "").lower()

    part1, part2 = _split_query_parts(original_query)

    part1_normalized = clean_search_query(_remove_accents(part1))
    part2_normalized = clean_search_query(_remove_accents(part2))
    track_normalized = _remove_accents(track_name)
    artist_normalized = _remove_accents(artist_name)

    score += _score_query_match(
        part1_normalized, part2_normalized, track_normalized, artist_normalized
    )
    score += _score_penalties(
        track_name, artist_name, artist_normalized, part1_normalized, part2_normalized
    )

    if result.get("mbid"):
        score += 5

    return score


def _score_query_match(part1: str, part2: str, track: str, artist: str) -> int:
    """Score how well the query parts match the track and artist.

    Uses early-exit on partial title matches intentionally: a partial match
    (score 50) combined with the -30 variant keyword penalty produces 20,
    which loses to a clean exact match (100). Aggregating title + artist scores
    would boost partial matches to 150, overwhelming those penalties.
    Artist correctness is instead enforced via the -100 penalty in _score_penalties.
    """
    if part2:
        # Case 1: part1=title, part2=artist
        if part1 == track and part2 == artist:
            return 100
        # Case 2: part1=artist, part2=title
        if part2 == track and part1 == artist:
            return 100
        # Partial match on part1 as title
        part1_score = _match_score(part1, track)
        if part1_score > 0:
            return part1_score
        # Partial match on part2 as title
        part2_score = _match_score(part2, track)
        if part2_score > 0:
            return part2_score
    else:
        # Single-part query
        single_score = _match_score(part1, track)
        if single_score > 0:
            return single_score

    # Fall back to word-level matching
    word_score = _word_match_score(part1, track)
    if word_score > 0:
        return word_score
    if part2:
        word_score = _word_match_score(part2, track)
        if word_score > 0:
            return word_score

    # No relation at all
    return -1000


def _score_penalties(
    track_name: str,
    artist_name: str,
    artist_normalized: str,
    part1_normalized: str,
    part2_normalized: str,
) -> int:
    """Apply penalty deductions for undesirable result attributes."""
    penalty = 0

    if artist_normalized not in part1_normalized and artist_normalized not in part2_normalized:
        penalty -= 100

    if artist_name in track_name:
        penalty -= 50

    for keyword in SPECIAL_VERSION_KEYWORDS:
        if keyword in track_name:
            penalty -= 30
            break

    if len(track_name) > 60:
        penalty -= 20

    return penalty


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _normalize_for_detection(text: str) -> str:
    """Normalize text for artist/title format detection."""
    return _remove_accents(clean_search_query(text.strip().lower()))


def _is_similar(a: str, b: str) -> bool:
    """Check if two strings are similar (exact or substring match)."""
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _detect_artist_first(original_query: str, artist: str, title: str) -> bool:
    """Detect if the original query uses 'Artist - Title' format."""
    part1_raw, part2_raw = _split_query_parts(original_query)
    if not part2_raw:
        return False

    part1 = _normalize_for_detection(part1_raw)
    part2 = _normalize_for_detection(part2_raw)
    artist_norm = _normalize_for_detection(artist)
    title_norm = _normalize_for_detection(title)

    part1_is_artist = _is_similar(part1, artist_norm)
    part1_is_title = _is_similar(part1, title_norm)
    part2_is_artist = _is_similar(part2, artist_norm)
    part2_is_title = _is_similar(part2, title_norm)

    # Both parts match their expected positions
    if part1_is_artist and part2_is_title:
        return True
    if part1_is_title and part2_is_artist:
        return False

    # Single-part matches on part1
    if part1_is_artist and not part1_is_title:
        return True
    if part1_is_title and not part1_is_artist:
        return False

    # Cross-reference with part2
    if part2_is_title and not part2_is_artist:
        return True
    if part2_is_artist and not part2_is_title:
        return False

    return False


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for artist/track comparison by removing punctuation."""
    normalized = re.sub(r"[._\-']", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _strip_artist_from_track(track_name: str, artist_name: str) -> str:
    """Remove artist name from track title if it's embedded.

    Last.fm sometimes returns track names like 'Artist - Title' or 'Artist-Title'.
    Handles variations like "a-ha" vs "A ha" where punctuation differs.
    """
    track_normalized = _normalize_for_comparison(track_name)
    artist_normalized = _normalize_for_comparison(artist_name)

    if track_normalized.startswith(artist_normalized + " "):
        separator_pattern = r"^.{0,50}?(?:\s*[-\u2013\u2014|:]\s*|\s+/\s+)(.+)$"
        match = re.match(separator_pattern, track_name)

        if match:
            before_separator = track_name[: match.start(1)].strip()
            before_separator = re.sub(r"\s*[-\u2013\u2014|:/]\s*$", "", before_separator)

            if _normalize_for_comparison(before_separator) == artist_normalized:
                return match.group(1)

    return track_name


def _preserve_original_artist(original_name: str, lastfm_artist: str) -> str | None:
    """Use the original artist from filename when Last.fm returned a subset.

    Last.fm only returns a single primary artist, but the original filename
    may credit multiple artists (e.g., 'Elton John & Kiki Dee'). If the
    Last.fm artist appears within the original artist part, preserve the original.
    """
    parts = re.split(r"\s+[-|]\s+", original_name, maxsplit=1)
    if len(parts) < 2:
        return None

    original_artist = parts[0].strip()
    lastfm_lower = lastfm_artist.lower()
    original_lower = original_artist.lower()

    has_multi_artist = any(sep in original_lower for sep in [" & ", " and ", ", "])
    if has_multi_artist and lastfm_lower in original_lower and lastfm_lower != original_lower:
        return original_artist

    # Preserve original artist when Last.fm returned the accent-stripped equivalent
    if _remove_accents(original_lower) == _remove_accents(lastfm_lower):
        return original_artist

    return None


def get_best_result(
    results: list[dict] | None, original_query: str, original_name: str | None = None
) -> str | None:
    """Select the highest-scoring result and format to match the input convention."""
    if not results:
        return None

    best = max(results, key=lambda r: score_result(r, original_query))

    # Strip parenthetical extras like "(feat. ...)" or "(Single 2014)" from track names
    clean_track_name = re.sub(r"\s*\([^)]*\)", "", best["name"])

    # Strip artist from track name if it's duplicated
    clean_track_name = _strip_artist_from_track(clean_track_name, best["artist"])

    # Preserve multi-artist credits from the original filename
    artist = best["artist"]
    if original_name:
        preserved = _preserve_original_artist(original_name, artist)
        if preserved:
            artist = preserved

    format_query = original_name or original_query
    if _detect_artist_first(format_query, best["artist"], clean_track_name):
        return f"{artist} - {clean_track_name}"

    return f"{clean_track_name} - {artist}"


# ---------------------------------------------------------------------------
# Last.fm API
# ---------------------------------------------------------------------------


def _lastfm_track_search(cleaned_query: str, limit: int | None = None) -> list[dict] | object:
    """Call Last.fm track.search with rate limiting and retry on rate limit errors.

    Returns track list on success, empty list on definitive failure (no results,
    API error), or _RATE_LIMITED sentinel on transient failures (timeout, network
    error, rate limiting).
    """
    global _last_api_request_time

    params = {
        "method": "track.search",
        "track": cleaned_query,
        "api_key": LASTFM_API_KEY,
        "format": "json",
    }
    if limit is not None:
        params["limit"] = str(limit)

    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
            logging.info(
                f"Rate limited by Last.fm, retrying in {backoff:.1f}s "
                f"(attempt {attempt + 1}/{_MAX_RETRIES})"
            )
            time.sleep(backoff)

        elapsed = time.time() - _last_api_request_time
        if elapsed < LASTFM_RATE_LIMIT:
            time.sleep(LASTFM_RATE_LIMIT - elapsed)

        try:
            response = requests.get(LASTFM_API_URL, params=params, timeout=5)
        except requests.exceptions.Timeout:
            logging.warning(f"Last.fm API request timed out for query: {cleaned_query}")
            return _RATE_LIMITED
        except requests.exceptions.RequestException as e:
            logging.error(f"Last.fm API request failed for query: {cleaned_query}: {e}")
            return _RATE_LIMITED
        finally:
            _last_api_request_time = time.time()

        if response.status_code == 429:
            logging.warning(f"Last.fm returned HTTP 429 for query: {cleaned_query}")
            continue

        if response.status_code != 200:
            logging.warning(
                f"Last.fm API returned status {response.status_code} for query: {cleaned_query}"
            )
            return []

        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            logging.error(f"Last.fm API returned invalid JSON for query: {cleaned_query}")
            logging.error(f"Response text: {response.text[:200]}")
            return []

        # Last.fm returns HTTP 200 even for API-level errors; check the error field
        if "error" in data:
            error_code = data.get("error")
            error_msg = data.get("message", "Unknown error")
            if error_code == 29:
                logging.warning(f"Last.fm rate limit exceeded for query: {cleaned_query}")
                continue
            logging.warning(
                f"Last.fm API error {error_code} ({error_msg}) for query: {cleaned_query}"
            )
            return []

        return data.get("results", {}).get("trackmatches", {}).get("track", [])

    logging.warning(f"Last.fm rate limit retries exhausted for query: {cleaned_query}")
    return _RATE_LIMITED


# Cache for lookup_lastfm: maps song filename -> corrected name.
# Uses a plain dict instead of lru_cache so that rate-limited None results
# are not cached (they should be retried on next request).
_song_name_cache: dict[str, str | None] = {}


def clear_song_name_cache() -> None:
    """Clear the song name lookup cache."""
    _song_name_cache.clear()


def lookup_lastfm(song: str) -> str | None:
    """Look up the canonical song name via the Last.fm API.

    Definitive results (including genuine "no results") are cached for the
    lifetime of the process. Rate-limited failures are NOT cached so the
    lookup is retried on the next request.
    """
    if song in _song_name_cache:
        return _song_name_cache[song]

    cleaned_query = clean_search_query(song)
    results = _lastfm_track_search(cleaned_query)

    if not isinstance(results, list):
        return None

    result = get_best_result(results, cleaned_query, original_name=song) if results else None
    _song_name_cache[song] = result
    return result


# ---------------------------------------------------------------------------
# regex_tidy and provenance helpers
# ---------------------------------------------------------------------------


def _extract_attribution_artist(name: str) -> str | None:
    """Try to extract an artist from karaoke attribution phrases."""
    for pattern in ATTRIBUTION_PATTERNS:
        match = pattern.search(name)
        if match:
            return match.group(1).strip()
    return None


def _strip_attribution_and_noise(name: str) -> str:
    """Remove the matched attribution phrase and trailing noise from the title."""
    for pattern in ATTRIBUTION_PATTERNS:
        name = pattern.sub("", name)
    # Strip remaining trailing bracketed/parenthesised content
    name = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", name)
    for noise_pat in TRAILING_NOISE_PATTERNS:
        prev = None
        while prev != name:
            prev = name
            name = noise_pat.sub("", name)
    return name.strip()


def regex_tidy(filename: str) -> str:
    """Clean a song filename using regex-only heuristics (no API calls).

    1. Strip emoji, replace underscores
    2. Extract attribution artist if present -> restructure as "Title - Artist"
    3. Otherwise strip trailing noise
    4. Normalize separators and whitespace
    """
    name = EMOJI_PATTERN.sub("", filename)
    name = name.replace("_", " ")

    # Try to extract artist from attribution phrases
    artist = _extract_attribution_artist(name)
    if artist:
        title = _strip_attribution_and_noise(name)
        name = f"{title} - {artist}"
    else:
        # Strip trailing parenthesised/bracketed content
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
        name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name)
        # Iteratively strip trailing noise patterns
        for noise_pat in TRAILING_NOISE_PATTERNS:
            prev = None
            while prev != name:
                prev = name
                name = noise_pat.sub("", name)

    # Normalize separators: en-dash/em-dash -> " - "
    name = re.sub(r"\s*[\u2013\u2014]\s*", " - ", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Strip trailing dash left over from noise removal
    name = re.sub(r"\s*-\s*$", "", name)
    return name


def has_youtube_id(filename: str) -> bool:
    """Detect if a filename contains a YouTube ID in PiKaraoke or yt-dlp format.

    Checks the raw filename including extension.
    """
    basename = os.path.basename(filename)
    # PiKaraoke format: Title---xxxxxxxxxxx.ext
    if re.search(r"---[A-Za-z0-9_-]{11}\.[^.]+$", basename):
        return True
    # yt-dlp format: Title [xxxxxxxxxxx].ext
    if re.search(r"\[[A-Za-z0-9_-]{11}\]\.[^.]+$", basename):
        return True
    return False


def has_artist_title_separator(name: str) -> bool:
    """Check if a cleaned name contains an artist-title separator (' - ')."""
    return " - " in name


def search_lastfm_tracks(query: str, limit: int | None = None) -> list[dict]:
    """Search Last.fm for tracks matching a query.

    Returns a list of {"name": ..., "artist": ...} dicts, or [] on failure.
    """
    cleaned = clean_search_query(query)
    results = _lastfm_track_search(cleaned, limit=limit)
    if not isinstance(results, list):
        return []
    return [{"name": r.get("name", ""), "artist": r.get("artist", "")} for r in results]


def get_song_correct_name(song: str, raw_filename: str | None = None) -> str | None:
    """Get the best corrected name for a song, using provenance-based routing.

    For YouTube-sourced files (detected via raw_filename):
      - regex_tidy() first; if it produces "Artist - Title", use that (fast, no API)
      - Otherwise fall through to lookup_lastfm (to find the artist)
    For non-YouTube files:
      - Always use lookup_lastfm
    """
    if raw_filename and has_youtube_id(raw_filename):
        tidied = regex_tidy(song)
        if has_artist_title_separator(tidied):
            return tidied
        # No separator — need Last.fm to find the artist
        return lookup_lastfm(song)

    return lookup_lastfm(song)
