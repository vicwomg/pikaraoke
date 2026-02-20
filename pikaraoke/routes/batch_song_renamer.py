from __future__ import annotations

import logging
import os
import re
import time
import unicodedata

import flask_babel
import requests
from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
)
from flask_paginate import Pagination, get_page_parameter

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

batch_song_renamer_bp = Blueprint("batch_song_renamer", __name__)

RESULTS_PER_PAGE = 10

LASTFM_API_KEY = "058c382f5fd686b4146f6028961c14da"
LASTFM_API_URL = "http://ws.audioscrobbler.com/2.0/"
# Last.fm rate limit: approximately 5 requests/second (conservative estimate)
# Adjust this if you experience rate limiting errors (increase value to slow down)
LASTFM_RATE_LIMIT = 0.2  # seconds between requests (5 req/sec)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds: retries at 1s, 2s

# Sentinel distinguishing "rate limited" (transient) from "no results" (definitive)
_RATE_LIMITED = object()

_last_api_request_time = 0.0

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

table_lines_template = """

{% for song in songs %}
<tr>
    <td class="vertical-align-middle col-num px-2">{{ loop.index + skip }}</td>
    <td class="vertical-align-middle col-old-name px-1 old-name">{{ filename_from_path(song.file) }}</td>
    <td class="vertical-align-middle col-new-name pr-0"><input class="input new-name
        {% if song.correct_name and song.is_equal %}
            is-success
        {% elif song.correct_name and not song.is_equal %}
            is-warning
        {% else %}
            is-danger
        {% endif %}" type="text" value="{{ song.correct_name or 'N/A' }}" data-new-name="{{ song.correct_name or 'N/A' }}" data-old-name="{{ filename_from_path(song.file) }}" /></td>
    <td class="vertical-align-middle col-btn pr-2">
    <div class="buttons are-small is-flex-wrap-nowrap">
		  <a class="accept-change button has-text-weight-bold has-text-success is-small "
    			href="#" data-old-name="{{ song.file }}"
				title="{% trans %}Accept suggested name{% endtrans %}"
            {{ 'disabled' if song.is_equal or song.correct_name == none else '' }}>
			  <i class="icon icon-ok"></i>
        </a>
        </div>
    </td>
</tr>
{% endfor %}

"""

all_songs_template = """
{{ pagination.links }} {{ pagination.info }}
<div id="loading" style="display:none">
	<div class="skeleton-block is-flex is-justify-content-center is-align-items-center mb-3" style="height: 10rem">
		<p class="has-text-white">
			{# MSG: Message displaying that the songs are currently being loaded #} {% trans %}Loading songs...{% endtrans %}
		</p>
	</div>
</div>
<table id="results-table" class="songs-table">
<tbody>
{{ table_lines|safe }}
</tbody>
</table>
{{ pagination.links }}
"""

songs_to_rename_template = """
{% if page|int == 1 %}
<table id="results-table" class="songs-table">
<tbody>
{% endif %}

{{ table_lines|safe }}

{% if page|int == 1 %}
</tbody>
</table>
{% endif %}

<div id="loading" style="display:none">
	<div class="skeleton-block is-flex is-justify-content-center is-align-items-center mb-3" style="height: 10rem">
		<p class="has-text-white">
			{# MSG: Message displaying that the songs are currently being loaded #} {% trans %}Loading songs...{% endtrans
			%}
		</p>
	</div>
</div>
<button id="load-more-songs" class="button is-fullwidth has-text-primary" data-page="{{ page }}" data-last-song-index="{{ song_index }}">{# MSG: Label of the button to load more songs #} {% trans %}Load more songs{% endtrans %}</button>
"""


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


def clean_search_query(song_name: str) -> str:
    """Strip noise words, brackets, and emoji to isolate the core song identity."""
    song_name = EMOJI_PATTERN.sub("", song_name)
    song_name = song_name.replace("_", " ")
    song_name = re.sub(r"\([^)]*\)", "", song_name)
    song_name = re.sub(r"\[[^\]]*\]", "", song_name)

    song_name = NOISE_PATTERN.sub("", song_name)
    song_name = re.sub(r"\s+", " ", song_name.strip())

    return song_name


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
    # e.g. original "Céline Dion" should not be downgraded to Last.fm's "Celine Dion"
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


def _lastfm_track_search(cleaned_query: str) -> list[dict] | object:
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


# Cache for get_song_correct_name: maps song filename -> corrected name.
# Uses a plain dict instead of lru_cache so that rate-limited None results
# are not cached (they should be retried on next request).
_song_name_cache: dict[str, str | None] = {}


def clear_song_name_cache() -> None:
    """Clear the song name lookup cache."""
    _song_name_cache.clear()


def get_song_correct_name(song: str) -> str | None:
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


def _normalize_name_for_comparison(name: str) -> str:
    """Normalize for comparison: unify dashes, whitespace, case, and diacritics."""
    if not name:
        return ""
    name = re.sub(r"[-\u2013\u2014\u2212]", "-", name)
    name = re.sub(r"\s+", " ", name.strip())
    return _remove_accents(name).lower()


def _names_match(name: str, correct_name: str | None) -> bool:
    """Check if a song name and its corrected version are effectively identical."""
    normalized_name = _normalize_name_for_comparison(name)
    normalized_correct = _normalize_name_for_comparison(correct_name or "")
    return normalized_name == normalized_correct


def _error_response(message: str) -> dict:
    return {"success": False, "message": message, "categoryClass": "is-danger"}


@batch_song_renamer_bp.route("/batch-song-renamer", methods=["GET"])
def browse():
    if not is_admin():
        return redirect(url_for("files.browse"))

    site_name = get_site_name()
    show_all_songs = request.args.get("show_all_songs") == "true"
    page = int(request.args.get("page", 1))

    # MSG: Title of the button to accept the suggested name
    _("Accept suggested name")

    return render_template(
        "batch-song-renamer.html",
        page=page,
        show_all_songs=show_all_songs,
        site_title=site_name,
        # MSG: Title of the files page.
        title=_("Batch Song Renamer"),
    )


@batch_song_renamer_bp.route("/batch-song-renamer/get-all-songs", methods=["GET"])
def get_all_songs():
    if not is_admin():
        return redirect(url_for("files.browse"))

    page = request.args.get(get_page_parameter(), type=int, default=1)
    start_index = (page - 1) * RESULTS_PER_PAGE

    k = get_karaoke_instance()
    available_songs = k.song_manager.songs

    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(available_songs),
        record_name="songs",
        per_page=RESULTS_PER_PAGE,
        href="batch-song-renamer?show_all_songs=true&page={0}",
    )

    songs = []
    for song in available_songs[start_index : start_index + RESULTS_PER_PAGE]:
        song_name = k.song_manager.filename_from_path(song)
        correct_name = get_song_correct_name(song_name)
        is_equal = _names_match(song_name, correct_name)
        songs.append({"file": song, "correct_name": correct_name, "is_equal": is_equal})

    table_lines_html = render_template_string(table_lines_template, songs=songs, skip=start_index)
    html = render_template_string(
        all_songs_template, pagination=pagination, table_lines=table_lines_html
    )

    return jsonify({"html": html})


@batch_song_renamer_bp.route("/batch-song-renamer/get-songs-to-rename", methods=["GET"])
def get_songs_to_rename():
    if not is_admin():
        return redirect(url_for("files.browse"))

    song_index = int(request.args.get("song_index") or 0)
    page = int(request.args.get("page") or 0)

    k = get_karaoke_instance()
    available_songs = k.song_manager.songs

    songs = []
    display_offset = page * RESULTS_PER_PAGE

    while len(songs) < RESULTS_PER_PAGE and song_index < len(available_songs):
        song = available_songs[song_index]
        song_name = k.song_manager.filename_from_path(song)
        correct_name = get_song_correct_name(song_name)
        song_index += 1

        if _names_match(song_name, correct_name):
            continue

        songs.append({"file": song, "correct_name": correct_name, "is_equal": False})

    table_lines_html = render_template_string(
        table_lines_template, songs=songs, skip=display_offset
    )
    html = render_template_string(
        songs_to_rename_template, table_lines=table_lines_html, page=page + 1, song_index=song_index
    )

    return jsonify({"html": html, "page": page + 1, "song_index": song_index})


@batch_song_renamer_bp.route("/batch-song-renamer/rename-song", methods=["POST"])
def rename_song():
    k = get_karaoke_instance()
    form_data = request.form.to_dict()

    if "new_name" not in form_data or "old_name" not in form_data:
        # MSG: Message shown after trying to edit a song without specifying the filename.
        return jsonify(_error_response(_("Error: No filename parameters were specified!")))

    new_name = form_data["new_name"].strip()
    old_name = form_data["old_name"]

    if k.queue_manager.is_song_in_queue(old_name):
        # MSG: Message shown after trying to edit a song that is in the queue.
        return jsonify(
            _error_response(
                _("Error: Can't edit this song because it is in the current queue: ") + old_name
            )
        )

    file_extension = os.path.splitext(old_name)[1]
    old_filename = k.song_manager.filename_from_path(old_name, remove_youtube_id=False)
    new_full_path = os.path.join(k.song_manager.download_path, new_name + file_extension)
    is_case_only_rename = old_filename.lower() == new_name.lower() and old_filename != new_name

    # Block renaming to an existing file (unless it's just a case change)
    if os.path.isfile(new_full_path) and not is_case_only_rename:
        # MSG: Message shown after trying to rename a file to a name that already exists.
        return jsonify(
            _error_response(
                _("Error renaming file: '%s' to '%s', Filename already exists")
                % (old_name, new_name + file_extension)
            )
        )

    if is_case_only_rename:
        # Two-step rename for case-insensitive filesystems (e.g. Windows)
        temp_name = f"{new_name}_temp_{int(time.time() * 1000)}"
        k.song_manager.rename(old_name, temp_name)
        temp_path = os.path.join(k.song_manager.download_path, temp_name + file_extension)
        k.song_manager.rename(temp_path, new_name)
    else:
        k.song_manager.rename(old_name, new_name)

    return jsonify({"success": True, "new_file_name": new_full_path})
