import os
import re
import time
import unicodedata

import flask_babel
from flask import (
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
)
from flask_paginate import Pagination
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.metadata_parser import clear_song_name_cache, get_song_correct_name

_ = flask_babel.gettext

batch_song_renamer_bp = Blueprint("batch_song_renamer", __name__)


class GetSongsToRenameQuery(Schema):
    song_index = fields.Integer(load_default=0, metadata={"description": "Starting song index"})
    page = fields.Integer(load_default=0, metadata={"description": "Current page number"})


class RenameSongForm(Schema):
    new_name = fields.String(required=True, metadata={"description": "New name for the song file"})
    old_name = fields.String(
        required=True, metadata={"description": "Full path of the song file to rename"}
    )


RESULTS_PER_PAGE = 10

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


def _normalize_name_for_comparison(name: str) -> str:
    """Normalize for comparison: unify dashes, whitespace, case, and diacritics."""
    if not name:
        return ""
    name = re.sub(r"[-\u2013\u2014\u2212]", "-", name)
    name = re.sub(r"\s+", " ", name.strip())
    return unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii").lower()


def _names_match(name: str, correct_name: str | None) -> bool:
    """Check if a song name and its corrected version are effectively identical."""
    normalized_name = _normalize_name_for_comparison(name)
    normalized_correct = _normalize_name_for_comparison(correct_name or "")
    return normalized_name == normalized_correct


def _error_response(message: str) -> dict:
    return {"success": False, "message": message, "categoryClass": "is-danger"}


@batch_song_renamer_bp.route("/batch-song-renamer", methods=["GET"])
def browse():
    """Batch song renamer page."""
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


@batch_song_renamer_bp.route("/batch-song-renamer/get-all-songs/<int:page>", methods=["GET"])
def get_all_songs(page):
    """Get all songs with suggested renames."""
    if not is_admin():
        return redirect(url_for("files.browse"))

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
        correct_name = get_song_correct_name(song_name, raw_filename=song)
        is_equal = _names_match(song_name, correct_name)
        songs.append({"file": song, "correct_name": correct_name, "is_equal": is_equal})

    table_lines_html = render_template_string(table_lines_template, songs=songs, skip=start_index)
    html = render_template_string(
        all_songs_template, pagination=pagination, table_lines=table_lines_html
    )

    return jsonify({"html": html})


@batch_song_renamer_bp.route("/batch-song-renamer/get-songs-to-rename", methods=["GET"])
@batch_song_renamer_bp.arguments(GetSongsToRenameQuery, location="query")
def get_songs_to_rename(query):
    """Get songs that have rename suggestions different from their current name."""
    if not is_admin():
        return redirect(url_for("files.browse"))

    song_index = query["song_index"]
    page = query["page"]

    k = get_karaoke_instance()
    available_songs = k.song_manager.songs

    songs = []
    display_offset = page * RESULTS_PER_PAGE

    while len(songs) < RESULTS_PER_PAGE and song_index < len(available_songs):
        song = available_songs[song_index]
        song_name = k.song_manager.filename_from_path(song)
        correct_name = get_song_correct_name(song_name, raw_filename=song)
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
@batch_song_renamer_bp.arguments(RenameSongForm, location="form")
def rename_song(form):
    """Rename a song file."""
    k = get_karaoke_instance()

    if "new_name" not in form or "old_name" not in form:
        # MSG: Message shown after trying to edit a song without specifying the filename.
        return jsonify(_error_response(_("Error: No filename parameters were specified!")))

    new_name = form["new_name"].strip()
    old_name = form["old_name"]

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
