"""File management routes for browsing, editing, and deleting songs."""

from __future__ import annotations

import logging
import os
import unicodedata

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.constants import ITUNES_COUNTRIES
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.metadata_parser import youtube_id_suffix

_ = flask_babel.gettext

# DB format values that have a matching icon in static/images/formats/
_FORMAT_ICONS = {"mp4", "avi", "mkv", "mov", "webm", "cdg", "ass"}
# Zipped CDG+MP3 packages are stored as "zip" in the DB but use the CDG icon
_FORMAT_ALIASES = {"zip": "cdg"}


def _format_icon(song_path: str, db_format: str | None) -> str | None:
    """Return the format icon filename (without extension) for a song, or None."""
    if youtube_id_suffix(song_path):
        return "youtube"
    if db_format:
        if db_format in _FORMAT_ICONS:
            return db_format
        if db_format in _FORMAT_ALIASES:
            return _FORMAT_ALIASES[db_format]
    return None


files_bp = Blueprint("files", __name__)

# Page sizes the browse page offers. browse_results_per_page is stored as a
# plain number and Settings still takes one, so a value from outside this list
# is added to it rather than leaving the dropdown unable to show what is set.
# One stored value, two controls that can always represent it.
_PER_PAGE_SIZES = [25, 50, 100, 250, 500, 1000]


def _per_page_options(current: int) -> list[int]:
    """The sizes on offer, always including the one in force."""
    return sorted(set(_PER_PAGE_SIZES) | {current})


class SongReferrerQuery(Schema):
    song = fields.String(required=True, metadata={"description": "Path to the song file"})
    referrer = fields.String(metadata={"description": "URL to redirect back to"})


class EditFileForm(Schema):
    new_file_name = fields.String(
        required=True, metadata={"description": "New filename (without extension)"}
    )
    old_file_name = fields.String(
        required=True, metadata={"description": "Current full path of the song file"}
    )
    referrer = fields.String(metadata={"description": "URL to redirect back to after editing"})


@files_bp.route("/browse", methods=["GET"])
def browse():
    """Browse available songs page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    # Clamped: the pager renders a link to the page either side of this one, and
    # a page of zero would index the song list from the wrong end.
    page = max(1, int(request.args.get("page", 1)))

    available_songs = k.song_manager.songs

    letter = request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = k.song_manager.display_name_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = k.song_manager.display_name_from_path(song).lower()
                # Normalize accented characters so e.g. "Édith" matches "e"
                normalized = unicodedata.normalize("NFD", f)
                base_char = normalized[0] if normalized else ""
                if base_char == letter.lower():
                    result.append(song)
        available_songs = result

    if request.args.get("sort") == "date":
        songs = sorted(available_songs, key=lambda x: os.path.getmtime(x))
        songs.reverse()
        sort_order = "Date"
    else:
        songs = available_songs
        sort_order = "Alphabetical"

    results_per_page = k.browse_results_per_page

    args = request.args.copy()
    args.pop("_", None)

    current_url = url_for("files.browse", **args.to_dict())

    # The pager's links carry the rest of the query string, so paging never
    # drops the letter filter or the sort order. The number is substituted into
    # a rendered URL rather than appended, which would have to guess whether the
    # separator is "?" or "&".
    args["page"] = "PAGENUMBER"
    page_href = url_for("files.browse", **args.to_dict())

    start_index = (page - 1) * results_per_page
    return render_template(
        "files.html",
        sort_order=sort_order,
        site_title=site_name,
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(),
        current_url=current_url,
        page=page,
        per_page=results_per_page,
        per_page_options=_per_page_options(results_per_page),
        total=len(songs),
        skip=start_index,
        page_href=page_href,
    )


@files_bp.route("/files/delete", methods=["GET"])
@files_bp.arguments(SongReferrerQuery, location="query")
def delete_file(query):
    """Delete a song file."""
    k = get_karaoke_instance()
    song_path = query["song"]
    referrer = query.get("referrer") or url_for("files.browse")
    if not is_admin():
        flash(_("You don't have permission to delete songs"), "is-danger")
        return redirect(referrer)
    if k.queue_manager.is_song_in_queue(song_path):
        flash(
            # MSG: Message shown after trying to delete a song that is in the queue.
            _("Error: Can't delete this song because it is in the current queue")
            + ": "
            + song_path,
            "is-danger",
        )
    else:
        k.song_manager.delete(song_path)
        # MSG: Message shown after deleting a song. Followed by the song path
        flash(
            _("Song deleted: %s") % k.song_manager.display_name_from_path(song_path),
            "is-warning",
        )
    return redirect(referrer)


@files_bp.route("/files/edit", methods=["GET"])
@files_bp.arguments(SongReferrerQuery, location="query")
def edit_file(query):
    """Show the song rename page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    song_path = query["song"]
    referrer = query.get("referrer") or url_for("files.browse")
    if not is_admin():
        flash(_("You don't have permission to edit songs"), "is-danger")
        return redirect(referrer)
    if k.queue_manager.is_song_in_queue(song_path):
        # MSG: Message shown after trying to edit a song that is in the queue.
        flash(
            _("Error: Can't edit this song because it is in the current queue: ") + song_path,
            "is-danger",
        )
        return redirect(referrer)
    raw_stem = k.song_manager.filename_from_path(song_path, tidy=False)
    format_icon = _format_icon(song_path, k.db.get_format(song_path))
    itunes_search_country = k.preferences.get_or_default("itunes_search_country")
    return render_template(
        "edit.html",
        site_title=site_name,
        title="Song File Edit",
        song=song_path,
        raw_stem=raw_stem,
        format_icon=format_icon,
        referrer=referrer,
        itunes_countries=ITUNES_COUNTRIES,
        itunes_search_country=itunes_search_country,
    )


@files_bp.route("/files/edit", methods=["POST"])
@files_bp.arguments(EditFileForm, location="form")
def rename_file(form):
    """Process a song rename."""
    k = get_karaoke_instance()
    referrer = form.get("referrer") or url_for("files.browse")
    new_name = form["new_file_name"]
    old_name = form["old_file_name"]
    if not is_admin():
        flash(_("You don't have permission to edit songs"), "is-danger")
    yt_suffix = youtube_id_suffix(old_name)
    new_name_full = new_name + yt_suffix
    if k.queue_manager.is_song_in_queue(old_name):
        # check one more time just in case someone added it during editing
        # MSG: Message shown after trying to edit a song that is in the queue.
        flash(
            _("Error: Can't edit this song because it is in the current queue: ") + old_name,
            "is-danger",
        )
    else:
        file_extension = os.path.splitext(old_name)[1]
        if os.path.isfile(
            os.path.join(k.song_manager.download_path, new_name_full + file_extension)
        ):
            flash(
                # MSG: Message shown after trying to rename a file to a name that already exists.
                _("Error renaming file: '%s' to '%s', Filename already exists")
                % (old_name, new_name_full + file_extension),
                "is-danger",
            )
        else:
            try:
                k.song_manager.rename(old_name, new_name_full)
            except OSError as e:
                logging.error(f"Error renaming file: {e}")
                flash(
                    _("Error renaming file: '%s' to '%s', %s") % (old_name, new_name_full, e),
                    "is-danger",
                )
            else:
                flash(
                    # MSG: Message shown after renaming a file.
                    _("Renamed file: %s to %s") % (old_name, new_name_full),
                    "is-warning",
                )
    return redirect(referrer)
