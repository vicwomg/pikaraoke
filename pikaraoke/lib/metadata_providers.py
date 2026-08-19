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
    _FEAT_CREDIT_RE,
    _SPECIAL_VERSION_RE,
    ATTRIBUTION_CONNECTORS,
    is_discardable_qualifier,
    normalize_for_comparison,
    regex_tidy,
    remove_accents,
    sanitize_filename,
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

# Stylized character substitutions used by artists (P!nk -> Pink, Ke$ha -> Kesha)
_STYLIZED_CHARS = str.maketrans({"!": "i", "$": "s"})


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
    text = text.translate(_STYLIZED_CHARS)
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
    query_parts_norm: list[tuple[str, str]],
    featuring_norm: str = "",
) -> int:
    """Score a suggestion result for relevance, version quality, and genre.

    Args:
        query_parts_norm: Pre-computed (raw, normalized) pairs from _normalize_query_parts.
        featuring_norm: Pre-normalized featuring artist string.
    """
    score = 0
    title_lower = result.get("title", "").lower()
    artist_lower = result.get("artist", "").lower()
    # Strip parenthetical qualifiers so "Fernando (Live)" matches as "Fernando"
    title_base = _PAREN_CONTENT_RE.sub("", title_lower).strip()

    # Normalize conjunctions for matching ("and"/"&"/etc.)
    artist_norm = _normalize_for_matching(artist_lower)
    title_norm = _normalize_for_matching(title_base)

    artist_matched = False
    title_matched = False
    artist_part_norm = ""
    for part, part_norm in query_parts_norm:
        matched_artist = _matches_field(part_norm, artist_norm)
        matched_title = (
            part == title_lower or part == title_base or _matches_field(part_norm, title_norm)
        )
        if matched_artist and not artist_part_norm:
            artist_part_norm = part_norm
        artist_matched |= matched_artist
        title_matched |= matched_title
        exact_match = (
            part_norm == artist_norm
            or part_norm == title_norm
            or part == title_lower
            or part == title_base
        )
        fuzzy = _fuzzy_match(part_norm, artist_norm) or _fuzzy_match(part_norm, title_norm)
        substring = part_norm in artist_norm or part_norm in title_norm
        if exact_match:
            score += 38
        elif fuzzy:
            score += 31
        elif substring:
            score += 19

    # Cross-field bonus: when one query part matched the artist and another
    # matched the title, the result aligns with the query's structure.
    # Without this, a result where both parts coincidentally appear in the
    # title (e.g. a track titled "Dolly Parton + Beer Cereal Divorce" by
    # David Liebe Hart) could outscore a result with the correct artist.
    # 24 rather than the proportional 23 so that two exact parts plus this
    # bonus sum to exactly _ALREADY_CORRECT.
    if len(query_parts_norm) >= 2 and artist_matched and title_matched:
        score += 24

    # Single-part decomposition: when the query has no separator but can be
    # split into artist + title, score each confirmed field independently.
    if len(query_parts_norm) == 1 and artist_matched and title_matched:
        part_norm = query_parts_norm[0][1]
        # Strip artist -> does remainder match title?
        artist_remainder = part_norm.replace(artist_norm, "", 1).strip()
        if artist_remainder and _matches_field(artist_remainder, title_norm):
            score += 31
        # Strip title -> does remainder match artist?
        title_remainder = part_norm.replace(title_norm, "", 1).strip()
        if title_remainder and _matches_field(title_remainder, artist_norm):
            score += 31

    # Bonus if the featuring artist appears in the result title
    # Normalize both sides so "and" matches "&" and accents are ignored
    if featuring_norm:
        title_full_norm = _normalize_for_matching(title_lower)
        if featuring_norm in title_full_norm:
            score += 12

    # Bonus when query artist part has extra names that appear in the result's
    # parenthetical (e.g. query "Taylor Swift and Ed Sheeran" matches
    # title "Everything Has Changed (feat. Ed Sheeran)").
    # This handles "and"/"&"/"with" collaborator names without treating them
    # as featuring keywords (which would break genuine duos).
    if len(query_parts_norm) >= 2 and title_base != title_lower:
        # Whichever part matched the artist, not whichever came first: the
        # filename may be "Title - Artist".
        query_artist_norm = artist_part_norm or query_parts_norm[0][1]
        parens_text = _extract_qualifier_text(title_lower)
        if parens_text and artist_norm != query_artist_norm:
            # Extract the extra part of the query artist beyond the result artist
            extra = query_artist_norm.replace(artist_norm, "").strip()
            extra = _LEADING_CONJUNCTION_RE.sub("", extra).strip()
            if extra and len(extra) > 2 and extra in parens_text:
                score += 12

    # Penalize titles with parenthetical qualifiers (sessions, performances, etc.)
    if title_base != title_lower:
        score -= 8

    # Penalize special versions (live, remix, etc.)
    if _SPECIAL_VERSION_RE.search(title_lower):
        score -= 23
    if len(title_lower) > 60:
        score -= 15

    # Prefer simple genres ("Rock") over compound ones ("Singer/Songwriter")
    genre = result.get("genre", "")
    if "/" in genre or "&" in genre:
        score -= 4
    return score


# A confirmed match is assigned its anchor rather than tallied: exactness is a
# definitive determination, so the version and length penalties that rank
# candidates against each other no longer apply. 100 is also what the rebased
# weights produce for a clean confirmed match (38 + 38 + 24), so the assignment
# agrees with the arithmetic instead of overriding it.
_ALREADY_CORRECT = 100  # the name as submitted is what we would rename it to
_ONE_CORRECTION = 98  # fields confirmed; one of order / noise / characters is wrong
_MANY_CORRECTIONS = 95  # fields confirmed; two or three of them are
_UNCONFIRMED_CEILING = 94  # nothing unconfirmed may enter the confirmed band


def _disqualifying_qualifiers(title: str) -> list[str]:
    """The qualifiers in the title that are neither a credit nor droppable noise.

    Round and square brackets alike: "[2011 Remaster]" renames to a different
    recording just as surely as "(2011 Remaster)" does.
    """
    kept = []
    for text in _extract_qualifier_texts(title):
        # A credit excuses only the bracket it is alone in: "(feat. Sia, Live)"
        # is still a live recording.
        credit_only = _FEAT_CREDIT_RE.match(text) and not _SPECIAL_VERSION_RE.search(text)
        if not credit_only and not is_discardable_qualifier(text):
            kept.append(text)
    return kept


def _has_disqualifying_qualifier(title: str) -> bool:
    """Whether the title carries a qualifier that is neither a credit nor droppable noise."""
    return bool(_disqualifying_qualifiers(title))


# The attribution phrases as they survive normalization, for reading a filename
# that names its artist in prose: "Cry Me a River made famous by Julie London".
_ATTRIBUTION_CONNECTORS_NORM = [_normalize_for_matching(c) for c in ATTRIBUTION_CONNECTORS]
_LEADING_ATTRIBUTION_RE = re.compile(r"^(?:" + "|".join(_ATTRIBUTION_CONNECTORS_NORM) + r")\s+")


def _decomposes_into(whole: str, first: str, second: str) -> bool:
    """Whether `whole` is exactly `first` then `second`, bar an attribution phrase."""
    if whole == f"{first} {second}":
        return True
    return any(whole == f"{first} {c} {second}" for c in _ATTRIBUTION_CONNECTORS_NORM)


def _part_names_field(part_norm: str, *field_norms: str) -> bool:
    """Whether a query part is any of these forms of a field.

    With or without an opening connector: "By the Way - Red Hot Chili Peppers"
    puts one at the front of a real title, and stripping it there would lose the
    match the separator already made plain.
    """
    forms = (part_norm, _LEADING_ATTRIBUTION_RE.sub("", part_norm))
    return any(f in forms for f in field_norms if f)


def _exact_match_artist_first(
    result: dict, query_parts_norm: list[tuple[str, str]], name: str
) -> bool | None:
    """Whether an exactly matching query is written artist-first.

    None when the result is not an exact match on both fields, including when
    its title carries a qualifier the name does not. A bracket the name says
    too is the track's own title -- "The Fox (What Does the Fox Say?)" -- not a
    variant the rename would swap in, so it disqualifies nothing.
    """
    if len(query_parts_norm) not in (1, 2):
        return None
    title_lower = result.get("title", "").lower()
    carried = {_normalize_for_matching(q) for q in _extract_qualifier_texts(name.lower())}
    qualifiers = _disqualifying_qualifiers(title_lower)
    if any(_normalize_for_matching(q) not in carried for q in qualifiers):
        return None
    artist_norm = _normalize_for_matching(result.get("artist", "").lower())
    title_norm = _normalize_for_matching(_PAREN_CONTENT_RE.sub("", title_lower).strip())
    if not artist_norm or not title_norm:
        return None
    # Both forms of the title. A filename already carrying the credit iTunes
    # writes -- "Everything Has Changed (feat. Ed Sheeran)" -- has to match the
    # whole title, not only what is left once the brackets come off. Comparing a
    # bracketed query part against a stripped title is what stopped this feature
    # recognising the very names it renames songs to.
    titles = (title_norm, _normalize_for_matching(title_lower))
    if len(query_parts_norm) == 1:
        # A query with no separator confirms both fields only when it is exactly
        # the two of them, nothing left over: "CAKE I Will Survive" and "Cry Me a
        # River by Julie London" qualify, "Cry Me a River, a Julie London song"
        # does not. Whichever arrangement matches is the order it is written in.
        whole = query_parts_norm[0][1]
        if any(_decomposes_into(whole, artist_norm, t) for t in titles):
            return True
        if any(_decomposes_into(whole, t, artist_norm) for t in titles):
            return False
        return None
    # "Cry Me a River - by Julie London": the separator is there, but the artist
    # side still opens with the connector the vendor wrote it with.
    first, second = query_parts_norm[0][1], query_parts_norm[1][1]
    if _part_names_field(first, artist_norm) and _part_names_field(second, *titles):
        return True
    if _part_names_field(first, *titles) and _part_names_field(second, artist_norm):
        return False
    return None


def _proposal(result: dict, artist_first: bool) -> str:
    """The filename a rename to this result would write.

    Sanitized, because that is what lands on disk: a score of "already correct"
    has to be measured against the name the save can actually produce, and the
    suggestion the user clicks has to be the same string it was scored as.
    """
    artist, title = result.get("artist", ""), result.get("title", "")
    return sanitize_filename(f"{artist} - {title}" if artist_first else f"{title} - {artist}")


def _without_shared_brackets(name: str, proposal: str) -> tuple[str, str]:
    """Both names with the bracketed groups they carry identically removed.

    regex_tidy reads every trailing bracket as noise, so a title bracketed in
    its own right -- "The Fox (What Does the Fox Say?)" -- would otherwise be
    read as a name wanting that bracket taken off, when the rename writes it
    straight back.
    """
    shared = set(_PAREN_CONTENT_RE.findall(name)) & set(_PAREN_CONTENT_RE.findall(proposal))
    for group in shared:
        name = name.replace(group, "")
        proposal = proposal.replace(group, "")
    return name.strip(), proposal.strip()


def _corrections(
    name: str, proposal: str, query_artist_first: bool, artist_first: bool
) -> list[str]:
    """Which kinds of correction a rename to `proposal` would apply.

    Three sequential transformations -- noise, then order, then characters --
    each asking "did this step change anything", so that one defect cannot be
    counted under two kinds. Noise goes first because reordering a name that
    still carries its brackets can split on a separator inside one.
    """
    kinds = []
    name, proposal = _without_shared_brackets(name, proposal)
    tidied = regex_tidy(name)
    if tidied != name:
        kinds.append("noise")
    reordered = tidied
    if query_artist_first != artist_first:
        head, _, tail = tidied.partition(" - ")
        if tail:
            reordered = f"{tail} - {head}"
    if reordered != tidied:
        kinds.append("order")
    # Deliberately raw: no case folding, no accent folding. This is what keeps
    # "Beyonce" off 100.
    if reordered != proposal:
        kinds.append("characters")
    return kinds


def _anchor_score(
    score: int, result: dict, query_parts_norm: list[tuple[str, str]], name: str, artist_first: bool
) -> tuple[int, str]:
    """Pin a confirmed match to its anchor; cap everything else below the band.

    `name` is the name the caller asked about, which on the edit page is what
    the box currently holds rather than what is on disk -- 100 answers "would
    saving this change anything", not "is the library tidy".

    Returns the score with the reason for it. A score below the band is only
    interpretable alongside which of the three ways of missing it applied.
    """
    query_artist_first = _exact_match_artist_first(result, query_parts_norm, name)
    if query_artist_first is None:
        # "iTunes offered a variant, not the track" and "the two fields were
        # never both matched" are the same 94 and want opposite responses.
        qualified = _has_disqualifying_qualifier(result.get("title", "").lower())
        return min(score, _UNCONFIRMED_CEILING), (
            "result qualified" if qualified else "unconfirmed"
        )
    proposal = _proposal(result, artist_first)
    # regex_tidy strips "(Live)" and "(2011 Remaster)" as readily as it strips
    # "(Official Video)", so a confirmed match can still be the wrong recording.
    if _has_disqualifying_qualifier(name) and not _has_disqualifying_qualifier(proposal):
        return min(score, _UNCONFIRMED_CEILING), "variant kept"
    kinds = _corrections(name, proposal, query_artist_first, artist_first)
    anchor = (_ALREADY_CORRECT, _ONE_CORRECTION, _MANY_CORRECTIONS, _MANY_CORRECTIONS)[len(kinds)]
    return anchor, ", ".join(kinds) or "already correct"


def _deduplicate_suggestions(
    results: list[dict], query: str, name: str, artist_first: bool, featuring: str = ""
) -> list[tuple[int, str, dict]]:
    """Keep the highest-scored result per unique (artist, title) pair.

    Anchoring happens here rather than at display time: the badge sits beside a
    ranked list, so a demotion applied after the sort could show a 95 above a 98.

    Returns (score, reason, result) tuples sorted by score descending.
    """
    query_parts_norm = _normalize_query_parts(query)
    featuring_norm = _normalize_for_matching(featuring) if featuring else ""
    best: dict[tuple[str, str], tuple[int, int, str, dict]] = {}
    for r in results:
        key = (r.get("artist", "").lower(), r.get("title", "").lower())
        tally = _suggestion_score(r, query_parts_norm, featuring_norm)
        score, reason = _anchor_score(tally, r, query_parts_norm, name, artist_first)
        if key not in best or (score, tally) > best[key][:2]:
            best[key] = (score, tally, reason, r)
    # The raw tally breaks ties: the ceiling flattens every unconfirmed result
    # above it to one value, and without this the best of them would rank by
    # nothing more than the order iTunes happened to return it in.
    ranked = sorted(best.values(), key=lambda x: (x[0], x[1]), reverse=True)
    return [(score, reason, result) for score, _, reason, result in ranked]


# Over-fetch factor to compensate for deduplication
_OVERFETCH_FACTOR = 3


def _extract_qualifier_texts(text: str) -> list[str]:
    """Each parenthetical and bracketed group's content, brackets removed."""
    return [g for groups in _PAREN_EXTRACT_RE.findall(text) for g in groups if g]


def _extract_qualifier_text(text: str) -> str:
    """Extract all parenthetical and bracketed content as a single string."""
    return " ".join(_extract_qualifier_texts(text))


def suggest_metadata(
    display_name: str,
    provider: MetadataProvider | None = None,
    limit: int = 5,
    artist_first: bool = True,
) -> list[dict]:
    """Tidy a display name and search for metadata suggestions.

    Args:
        artist_first: Render each suggestion as "Artist - Title" rather than
            "Title - Artist". The library's naming convention, not the current
            filename's, so suggestions pull an untidy library into line. It also
            decides which order scores 100, so the default mirrors the
            "artist_title" default of the suggestion_name_order preference.
    """
    if provider is None:
        provider = ITunesProvider()
    tidied = regex_tidy(display_name)
    # Strip "ft"/"feat" -- clutters search and scoring,
    # but extract the featuring artist name for a bonus signal
    feat_match = _FEATURING_PATTERN.search(tidied)
    featuring = feat_match.group("name").strip() if feat_match else ""
    search_query = _FEATURING_PATTERN.sub("", tidied).strip()
    results = provider.search(search_query, limit=limit * _OVERFETCH_FACTOR)
    # The untidied name, not the query: the question the anchors answer is what
    # is on disk, not what the tidy makes of it.
    scored = _deduplicate_suggestions(results, search_query, display_name, artist_first, featuring)
    truncated = scored[:limit]

    if not truncated:
        return []

    # Build output dicts with display, score and reason (don't mutate originals)
    return [
        {**r, "display": _proposal(r, artist_first), "score": max(0, score), "reason": reason}
        for score, reason, r in truncated
    ]
