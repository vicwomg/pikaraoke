import os

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

import re

batch_song_renamer_bp = Blueprint("batch_song_renamer", __name__)

results_per_page = 10

api_key = "058c382f5fd686b4146f6028961c14da"
url = "http://ws.audioscrobbler.com/2.0/"

# ---- Template HTML as string ----
table_lines_template = """

{% for song in songs %}
{% set equal = (song.correct_name == filename_from_path(song.file)) %}
<tr>
    <td class="vertical-align-middle col-num px-2">{{ loop.index + skip }}</td>
    <td class="vertical-align-middle col-old-name px-1 old-name">{{ filename_from_path(song.file) }}</td>
    <td class="vertical-align-middle col-new-name pr-0"><input class="input new-name
        {% if song.correct_name and song.correct_name == filename_from_path(song.file) %}
            is-success
        {% elif song.correct_name and song.correct_name != filename_from_path(song.file) %}
            is-warning
        {% else %}
            is-danger
        {% endif %}" type="text" value="{{ song.correct_name or 'N/A' }}" data-new-name="{{ song.correct_name or 'N/A' }}" data-old-name="{{ filename_from_path(song.file) }}" /></td>
    <td class="vertical-align-middle col-btn pr-2">
    <div class="buttons are-small is-flex-wrap-nowrap">
		  <a class="accept-change button has-text-weight-bold has-text-success is-small "
    			href="#" data-old-name="{{ song.file }}"
				title="{% trans %}Accept suggested name{% endtrans %}"
            {{ 'disabled' if equal or song.correct_name == none else '' }}>
			  <i class="icon icon-ok"></i>
        </a>
        <!--
        <a class="button is-small has-text-success"
           href="{{ url_for('files.edit_file') }}?song={{ song.file|urlencode }}"
           title="Edit song">
           <i class="icon icon-edit-1"></i>
        </a>
        -->
        </div>
    </td>
</tr>
{% endfor %}

"""

# ---- Template HTML as string ----
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

# ---- Template HTML as string ----
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


def clean_search_query(song_name):
    # Remove emojis
    emoji_pattern = re.compile(
        "["
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F700-\U0001F77F"  # alchemical symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002702-\U000027B0"  # Dingbats
        "\U000024C2-\U0001F251"
        "]+"
    )
    song_name = emoji_pattern.sub("", song_name)

    # Remove common suffixes that interfere with the search
    removals = [
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
        r"\blyrics\b",
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

    # Remove underscore
    song_name = song_name.replace("_", " ")

    # Remove content between parentheses and brackets
    song_name = re.sub(r"\([^)]*\)", "", song_name)
    song_name = re.sub(r"\[[^\]]*\]", "", song_name)

    for pattern in removals:
        song_name = re.sub(pattern, "", song_name, flags=re.IGNORECASE)

    return song_name.strip()


def score_result(result, original_query):
    score = 0

    # Check if the song name or the artist name is written entirely in uppercase and penalize
    if result.get("name", "").isupper() or result.get("artist", "").isupper():
        score -= 10

    track_name = result.get("name", "").lower()
    artist_name = result.get("artist", "").lower()

    # Remove acentos para comparação mais flexível
    import unicodedata

    def remove_accents(text):
        return "".join(
            c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
        )

    # Extract the parts of the query
    # 1st - Try to split by " - " or "  " (with spaces)
    query_parts = re.split(r"\s+[-|｜]\s+", original_query.lower())

    # 2nd - If there are not 2 parts, try to split by "-" or "" without spaces
    if len(query_parts) < 2:
        query_parts = re.split(r"\s*[-|｜]\s*", original_query.lower())

    # Take the first two parts (ignoring the third such as "album1")
    if len(query_parts) >= 2:
        part1 = query_parts[0].strip()
        part2 = query_parts[1].strip()
    else:
        part1 = original_query.lower().strip()
        part2 = ""

    # Normalize everything
    part1_normalized = clean_search_query(remove_accents(part1))
    part2_normalized = clean_search_query(remove_accents(part2))
    track_title_normalized = remove_accents(track_name)
    artist_name_normalized = remove_accents(artist_name)

    # MAIN SCORING: Check both combinations
    matched = False

    # Case 1: part1 is title, part2 is artist (e.g., "Viva La Vida - Coldplay")
    if part2:
        if (
            part1_normalized == track_title_normalized
            and part2_normalized == artist_name_normalized
        ):
            score += 100
            matched = True
        elif (
            part1_normalized == track_title_normalized or part1_normalized in track_title_normalized
        ):
            score += 50
            matched = True

    # Case 2: part1 is artist, part2 is title (ex: "Coldplay - Viva La Vida")
    if part2 and not matched:
        if (
            part2_normalized == track_title_normalized
            and part1_normalized == artist_name_normalized
        ):
            score += 100
            matched = True
        elif (
            part2_normalized == track_title_normalized or part2_normalized in track_title_normalized
        ):
            score += 50
            matched = True

    # Case 3: has just one part
    if not part2 and not matched:
        if part1_normalized == track_title_normalized:
            score += 100
            matched = True
        elif part1_normalized in track_title_normalized:
            score += 50
            matched = True

    # If none of the combinations worked, try matching by words
    if not matched:
        # Try with part1
        words_match = any(
            word in track_title_normalized for word in part1_normalized.split() if len(word) > 3
        )
        if words_match:
            score += 20
            matched = True

        # Try with part2
        if part2 and not matched:
            words_match = any(
                word in track_title_normalized for word in part2_normalized.split() if len(word) > 3
            )
            if words_match:
                score += 20
                matched = True

    # If there is no relation at all, penalize heavily
    if not matched:
        score -= 1000

    # Check if there is no artist in part 1 or part 2 for penalization
    if (
        artist_name_normalized not in part1_normalized
        and artist_name_normalized not in part2_normalized
    ):
        score -= 100

    # Penalize duplicated name (e.g., "Viva La Vida - Coldplay" by the artist "Coldplay")
    if artist_name in track_name.lower():
        score -= 50

    # Penalize special versions (we want the original)
    bad_keywords = [
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
    for keyword in bad_keywords:
        if keyword in track_name.lower():
            score -= 30
            break

    # Penalize very long titles
    if len(track_name) > 60:
        score -= 20

    # BONUS if it has MBID (usually more reliable)
    if result.get("mbid"):
        score += 5

    return score


def get_best_result(results, original_query):
    if not results:
        return None

    scored_results = []

    for result in results:
        score = score_result(result, original_query)
        scored_results.append((score, result))

    # Sort by score (highest first)
    scored_results.sort(reverse=True, key=lambda x: x[0])

    best = scored_results[0][1]

    return f"{best['name']} - {best['artist']}"


def get_song_correct_name(song):
    cleaned_query = clean_search_query(song)

    params = {
        "method": "track.search",
        "track": cleaned_query,
        "api_key": api_key,
        "format": "json",
        #  'limit': 10
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        return None

    data = response.json()
    results = data.get("results", {}).get("trackmatches", {}).get("track", [])

    if not results:
        return None

    return get_best_result(results, cleaned_query)


@batch_song_renamer_bp.route("/batch-song-renamer", methods=["GET", "POST"])
def browse():
    if not is_admin():
        return redirect(url_for("files.browse"))

    site_name = get_site_name()

    show_all_songs = "show_all_songs" in request.args and request.args["show_all_songs"] == "true"

    page = request.args.get(get_page_parameter(), type=int, default=1)

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
    start_index = (page - 1) * results_per_page

    skip = page * results_per_page - results_per_page

    k = get_karaoke_instance()
    available_songs = k.available_songs

    songs = []

    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(available_songs),
        record_name="songs",
        per_page=results_per_page,
        href="batch-song-renamer?show_all_songs=true&page={0}",
    )

    for song in available_songs[start_index : start_index + results_per_page]:
        song_name = k.filename_from_path(song)
        correct_name = get_song_correct_name(song_name)
        songs.append({"file": song, "correct_name": correct_name})

    table_lines_html = render_template_string(table_lines_template, songs=songs, skip=skip)
    html = render_template_string(
        all_songs_template, pagination=pagination, table_lines=table_lines_html
    )

    return jsonify(
        {
            "html": html,
        }
    )


@batch_song_renamer_bp.route("/batch-song-renamer/get-songs-to-rename", methods=["GET"])
def get_songs_to_rename():
    if not is_admin():
        return redirect(url_for("files.browse"))

    song_index = int(request.args.get("song-index") or 0)
    page = int(request.args.get("page") or 0)

    k = get_karaoke_instance()
    available_songs = k.available_songs

    songs = []

    i = 0
    max = 10
    skip = page * max

    while i < max:
        if song_index >= len(available_songs):
            break
        song = available_songs[song_index]
        song_name = k.filename_from_path(song)
        correct_name = get_song_correct_name(song_name)
        if song_name == correct_name:
            song_index += 1
            continue
        songs.append({"file": song, "correct_name": correct_name})
        song_index += 1
        i += 1

    table_lines_html = render_template_string(table_lines_template, songs=songs, skip=skip)
    html = render_template_string(
        songs_to_rename_template, table_lines=table_lines_html, page=page + 1, song_index=song_index
    )

    return jsonify({"html": html, "page": page + 1, "song-index": song_index})


@batch_song_renamer_bp.route("/batch-song-renamer/rename-song", methods=["POST"])
def rename_song():
    k = get_karaoke_instance()

    d = request.form.to_dict()

    queue_error_msg = ()

    if "new_name" in d and "old_name" in d:
        new_name = d["new_name"].strip()
        old_name = d["old_name"]

        if k.is_song_in_queue(old_name):
            # MSG: Message shown after trying to edit a song that is in the queue.
            queue_error_msg = {
                "success": False,
                "message": _("Error: Can't edit this song because it is in the current queue: ")
                + old_name,
                "categoryClass": "is-danger",
            }
        else:
            # check if new_name already exist
            file_extension = os.path.splitext(old_name)[1]
            if os.path.isfile(os.path.join(k.download_path, new_name + file_extension)):
                # MSG: Message shown after trying to rename a file to a name that already exists.
                queue_error_msg = {
                    "success": False,
                    "message": _("Error renaming file: '%s' to '%s', Filename already exists")
                    % (old_name, new_name + file_extension),
                    "categoryClass": "is-danger",
                }
            else:
                ext = os.path.splitext(old_name)
                k.rename(old_name, new_name)
                new_file_full_path = os.path.join(k.download_path, new_name + ext[1])
                queue_error_msg = {"success": True, "new_file_name": new_file_full_path}
    else:
        # MSG: Message shown after trying to edit a song without specifying the filename.
        return jsonify(
            {
                "success": False,
                "message": _("Error: No filename parameters were specified!"),
                "categoryClass": "is-danger",
            }
        )

    return jsonify(queue_error_msg)
