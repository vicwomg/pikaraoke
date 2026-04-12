"""Song metadata parsing: Last.fm lookup, regex tidying, and search scoring.

Pure library module — no Flask imports. Extracted from batch_song_renamer.py
to enable reuse in the SQLite enrichment pipeline.
"""

import logging
import os
import re
import time
import unicodedata

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
    "\U000024C2-\U000024FF"  # enclosed alphanumerics
    "\U00002600-\U000026FF"  # miscellaneous symbols
    "\U0001F200-\U0001F251"  # enclosed ideographic supplement
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

# Matches parenthesised content EXCEPT featuring credits like "(feat. X)" / "(ft. X)"
_FEAT_LOOKAHEAD = r"(?!\s*(?:feat(?:uring)?|ft)\.?\s)"
_PAREN_NOT_FEAT = re.compile(rf"\s*\({_FEAT_LOOKAHEAD}[^)]*\)", flags=re.IGNORECASE)
_PAREN_NOT_FEAT_TRAILING = re.compile(rf"\s*\({_FEAT_LOOKAHEAD}[^)]*\)\s*$", flags=re.IGNORECASE)

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
    "re-recorded",
    "rerecorded",
    "remastered",
    "encore",
    "deluxe",
    "bonus track",
    "demo",
]
_SPECIAL_VERSION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in SPECIAL_VERSION_KEYWORDS if kw != " - ") + r")\b",
    re.IGNORECASE,
)

LASTFM_API_KEY = "058c382f5fd686b4146f6028961c14da"
LASTFM_API_URL = "http://ws.audioscrobbler.com/2.0/"
# Last.fm rate limit: approximately 5 requests/second (conservative estimate)
LASTFM_RATE_LIMIT = 0.2  # seconds between requests (5 req/sec)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds: retries at 1s, 2s

# Sentinel distinguishing "rate limited" (transient) from "no results" (definitive)
_RATE_LIMITED = object()

_last_api_request_time = 0.0

# All words/phrases meaning "this is a karaoke/instrumental track."
# Adding a new language = adding strings to this list. Nothing else changes.
_KARAOKE_KEYWORDS = [
    # Global
    "karaoke",
    "karaokê",
    "instrumental",
    # Chinese (Traditional + Simplified)
    "卡拉OK",  # karaoke
    "KTV",
    "伴奏",  # accompaniment
    "純音樂",  # pure music / instrumental (trad)
    "纯音乐",  # pure music / instrumental (simp)
    "無人聲",  # no vocals (trad)
    "无人声",  # no vocals (simp)
    "導唱",  # guide vocal (trad)
    "导唱",  # guide vocal (simp)
    "消音",  # vocal removed
    # Japanese
    "カラオケ",
    "オフボーカル",  # off vocal
    "ボカロ",  # vocaloid
    # Korean
    "노래방",  # noraebang
    "금영",  # Keumyoung karaoke brand
    "태진",  # Taejin karaoke brand
]
_KARAOKE_KEYWORDS_ALT = "|".join(re.escape(kw) for kw in _KARAOKE_KEYWORDS)

# Leading noise: "KARAOKE - Title" or "Official Video | Title" etc.
_LEADING_NOISE = re.compile(
    rf"^(?:{_KARAOKE_KEYWORDS_ALT}|official\s+(?:music\s+)?video)\s*[-|:】》」』]\s*",
    re.IGNORECASE,
)
# Leading [square brackets] containing a karaoke keyword — strip entirely.
_LEADING_BRACKET_NOISE_RE = re.compile(
    rf"^\s*\[[^\]]*?(?:{_KARAOKE_KEYWORDS_ALT})[^\]]*?\]\s*",
    re.IGNORECASE,
)

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
    # Any karaoke keyword = nuke everything from that word to end of string.
    # Covers all languages via _KARAOKE_KEYWORDS — no per-language patterns needed.
    re.compile(rf"\s*(?:{_KARAOKE_KEYWORDS_ALT}).*$", re.IGNORECASE),
    # Production/metadata labels (any language) — strip at end of string only.
    # These are NOT karaoke synonyms, just generic video/audio labels.
    re.compile(
        r"\s*(?:"
        r"official\s+(?:music\s+)?video|lyrics?|hd|hq"
        r"|with\s+lyrics|no\s+lead\s+vocal|cc"  # English
        r"|翻唱|現場|现场|高清|歌詞|歌词|MV|原版"  # Chinese
        r"|歌ってみた|カバー"  # Japanese
        r"|TJ|MR"  # Korean
        r")[\s.!]*$",
        re.IGNORECASE,
    ),
]

# A dash adjacent to a CJK/Kana/Hangul character is always a separator (never a hyphen
# within a word). Uses lookaround so the replacement is just the dash, not surrounding chars.
_CJK = (
    r"\u4e00-\u9fff"  # CJK Unified Ideographs
    r"\u3400-\u4dbf"  # CJK Unified Ideographs Extension A
    r"\uf900-\ufaff"  # CJK Compatibility Ideographs
    r"\u3040-\u309f"  # Hiragana
    r"\u30a0-\u30ff"  # Katakana
    r"\uac00-\ud7af"  # Hangul Syllables
    r"\u1100-\u11ff"  # Hangul Jamo
    r"\u31f0-\u31ff"  # Katakana Phonetic Extensions
    r"\uff65-\uff9f"  # Halfwidth Katakana
)
_CJK_DASH_RE = re.compile(rf"(?<=[{_CJK}])\s*-\s*|\s*-\s*(?=[{_CJK}])")

# 【lenticular】and「corner」brackets contain metadata labels — strip entirely
_CJK_STRIP_LABEL_RE = re.compile(r"\s*(?:【[^】]*】|「[^」]*」)")
# 《double angle》and『white corner』brackets wrap titles — unwrap (keep content)
# unless the content is noise (KTV, karaoke labels), in which case strip entirely.
_CJK_UNWRAP_TITLE_RE = re.compile(r"[《『]([^》』]*)[》』]")
_CJK_TITLE_NOISE_RE = re.compile(_KARAOKE_KEYWORDS_ALT, re.IGNORECASE)


def _replace_cjk_title_bracket(match: re.Match) -> str:
    content = match.group(1)
    if _CJK_TITLE_NOISE_RE.search(content):
        return " "
    return f" {content} "


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def remove_accents(text: str) -> str:
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
    song_name = EMOJI_PATTERN.sub(" ", song_name)
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

    part1_normalized = clean_search_query(remove_accents(part1))
    part2_normalized = clean_search_query(remove_accents(part2))
    track_normalized = remove_accents(track_name)
    artist_normalized = remove_accents(artist_name)

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

    if " - " in track_name or _SPECIAL_VERSION_RE.search(track_name):
        penalty -= 30

    if len(track_name) > 60:
        penalty -= 20

    return penalty


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _normalize_for_detection(text: str) -> str:
    """Normalize text for artist/title format detection."""
    return remove_accents(clean_search_query(text.strip().lower()))


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


def normalize_for_comparison(text: str) -> str:
    """Normalize text for artist/track comparison by removing punctuation."""
    normalized = text.lower().replace("&", " and ")
    # Strip apostrophes entirely (possessives/contractions aren't word boundaries)
    normalized = normalized.replace("'", "").replace("\u2019", "")
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _strip_artist_from_track(track_name: str, artist_name: str) -> str:
    """Remove artist name from track title if it's embedded.

    Metadata APIs sometimes return track names like 'Artist - Title' or
    'Artist-Title'. Handles variations like "a-ha" vs "A ha" where
    punctuation differs.
    """
    track_normalized = normalize_for_comparison(track_name)
    artist_normalized = normalize_for_comparison(artist_name)

    if track_normalized.startswith(artist_normalized + " "):
        separator_pattern = r"^.{0,50}?(?:\s*[-\u2013\u2014|:]\s*|\s+/\s+)(.+)$"
        match = re.match(separator_pattern, track_name)

        if match:
            before_separator = track_name[: match.start(1)].strip()
            before_separator = re.sub(r"\s*[-\u2013\u2014|:/]\s*$", "", before_separator)

            if normalize_for_comparison(before_separator) == artist_normalized:
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
    if remove_accents(original_lower) == remove_accents(lastfm_lower):
        return original_artist

    return None


def get_best_result(
    results: list[dict] | None, original_query: str, original_name: str | None = None
) -> str | None:
    """Select the highest-scoring result and format to match the input convention."""
    if not results:
        return None

    best = max(results, key=lambda r: score_result(r, original_query))

    # Strip parenthetical extras like "(Single 2014)" but preserve featuring credits
    clean_track_name = _PAREN_NOT_FEAT.sub("", best["name"])

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

    import requests

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
    # Strip remaining trailing bracketed/parenthesised content, preserving featuring credits
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name)
    name = _PAREN_NOT_FEAT_TRAILING.sub("", name)
    for noise_pat in TRAILING_NOISE_PATTERNS:
        prev = None
        while prev != name:
            prev = name
            name = noise_pat.sub("", name)
    # Strip dangling separator or open paren/bracket left by noise removal
    # (e.g. "Title -" or "Title (") before the caller composes "Title - Artist"
    name = re.sub(r"\s*[-(\[]\s*$", "", name)
    return name.strip()


def _step_strip_emoji_and_underscores(name: str) -> str:
    name = EMOJI_PATTERN.sub(" ", name)
    return name.replace("_", " ")


def _step_strip_leading_noise(name: str) -> str:
    name = _LEADING_NOISE.sub("", name)
    return _LEADING_BRACKET_NOISE_RE.sub("", name)


def _step_normalize_cjk_brackets(name: str) -> str:
    name = _CJK_STRIP_LABEL_RE.sub(" ", name)
    return _CJK_UNWRAP_TITLE_RE.sub(_replace_cjk_title_bracket, name)


def _step_normalize_cjk_dashes(name: str) -> str:
    return _CJK_DASH_RE.sub(" - ", name)


def _step_extract_attribution_or_strip_noise(name: str) -> str:
    artist = _extract_attribution_artist(name)
    if artist:
        title = _strip_attribution_and_noise(name)
        return f"{title} - {artist}"
    # No attribution — strip trailing parenthesised/bracketed content + noise
    name = _PAREN_NOT_FEAT_TRAILING.sub("", name)
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name, flags=re.IGNORECASE)
    for noise_pat in TRAILING_NOISE_PATTERNS:
        prev = None
        while prev != name:
            prev = name
            name = noise_pat.sub("", name)
    return name


def _step_normalize_separators_and_whitespace(name: str) -> str:
    # Strip dangling open paren/bracket left by noise removal (e.g. "Title (")
    name = re.sub(r"\s*[\(\[]\s*$", "", name)
    # Normalize separators: en-dash, em-dash, big solidus, fullwidth solidus -> " - "
    name = re.sub(r"\s*[\u2013\u2014\u29f8\uff0f]\s*", " - ", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Strip trailing dash left over from noise removal
    return re.sub(r"\s*-\s*$", "", name)


def regex_tidy(filename: str) -> str:
    """Clean a song filename using regex-only heuristics (no API calls)."""
    name = _step_strip_emoji_and_underscores(filename)
    name = _step_strip_leading_noise(name)
    name = _step_normalize_cjk_brackets(name)
    name = _step_normalize_cjk_dashes(name)
    name = _step_extract_attribution_or_strip_noise(name)
    return _step_normalize_separators_and_whitespace(name)


def youtube_id_suffix(file_path: str) -> str:
    """Extract the YouTube ID suffix from a filename.

    Returns the suffix string (e.g. '---dQw4w9WgXcQ' or ' [dQw4w9WgXcQ]')
    or empty string if no YouTube ID is present. Operates on the stem
    (without extension) of the basename.
    """
    stem = os.path.splitext(os.path.basename(file_path))[0]
    match = re.search(r"(---[A-Za-z0-9_-]{11})$", stem)
    if match:
        return match.group(1)
    match = re.search(r"(\s*\[[A-Za-z0-9_-]{11}\])$", stem)
    if match:
        return match.group(1)
    return ""


def has_youtube_id(filename: str) -> bool:
    """Detect if a filename contains a YouTube ID in PiKaraoke or yt-dlp format."""
    return bool(youtube_id_suffix(filename))


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
