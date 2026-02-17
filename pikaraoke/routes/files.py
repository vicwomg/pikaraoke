"""File management routes for browsing, editing, and deleting songs."""

from __future__ import annotations

import logging
import os
import unicodedata
from urllib.parse import unquote

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_paginate import Pagination, get_page_parameter
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext


files_bp = Blueprint("files", __name__)


class SongReferrerQuery(Schema):
    song = fields.String(metadata={"description": "Path to the song file"})
    referrer = fields.String(metadata={"description": "URL to redirect back to"})


class EditFileForm(Schema):
    new_file_name = fields.String(metadata={"description": "New filename (without extension)"})
    old_file_name = fields.String(metadata={"description": "Current full path of the song file"})
    referrer = fields.String(metadata={"description": "URL to redirect back to after editing"})


@files_bp.route("/browse", methods=["GET"])
def browse():
    """Browse available songs page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    search = False
    q = request.args.get("q")
    if q:
        search = True
    page = int(request.args.get("page", 1))

    available_songs = k.song_manager.songs

    letter = request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = k.song_manager.filename_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = k.song_manager.filename_from_path(song).lower()
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

    page_param = get_page_parameter()
    args[page_param] = "{0}"

    args_dict = args.to_dict()
    pagination_href = unquote(url_for("files.browse", **args_dict))  # type: ignore

    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(songs),
        search=search,
        record_name="songs",
        per_page=results_per_page,
        display_msg="Showing <b>{start} - {end}</b> of <b>{total}</b> {record_name}",
        href=pagination_href,
    )
    start_index = (page - 1) * results_per_page
    return render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        site_title=site_name,
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(),
        current_url=current_url,
    )


@files_bp.route("/files/delete", methods=["GET"])
@files_bp.arguments(SongReferrerQuery, location="query")
def delete_file(query):
    """Delete a song file."""
    k = get_karaoke_instance()
    if "song" in query:
        song_path = query["song"]
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
                _("Song deleted: %s") % k.song_manager.filename_from_path(song_path), "is-warning"
            )
    else:
        # MSG: Message shown after trying to delete a song without specifying the song.
        flash(_("Error: No song specified!"), "is-danger")
    referrer = query.get("referrer") or url_for("files.browse")
    return redirect(referrer)


@files_bp.route("/files/edit", methods=["GET", "POST"])
@files_bp.arguments(SongReferrerQuery, location="query")
@files_bp.arguments(EditFileForm, location="form")
def edit_file(query, form):
    """Edit a song filename."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    # MSG: Message shown after trying to edit a song that is in the queue.
    queue_error_msg = _("Error: Can't edit this song because it is in the current queue: ")
    if "song" in query:
        song_path = query["song"]
        referrer = query.get("referrer") or url_for("files.browse")
        if k.queue_manager.is_song_in_queue(song_path):
            flash(queue_error_msg + song_path, "is-danger")
            return redirect(referrer)
        else:
            return render_template(
                "edit.html",
                site_title=site_name,
                title="Song File Edit",
                song=song_path,
                referrer=referrer,
            )
    else:
        referrer = form.get("referrer") or url_for("files.browse")
        if "new_file_name" in form and "old_file_name" in form:
            new_name = form["new_file_name"]
            old_name = form["old_file_name"]
            if k.queue_manager.is_song_in_queue(old_name):
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + old_name, "is-danger")
            else:
                # check if new_name already exist
                file_extension = os.path.splitext(old_name)[1]
                if os.path.isfile(
                    os.path.join(k.song_manager.download_path, new_name + file_extension)
                ):
                    flash(
                        # MSG: Message shown after trying to rename a file to a name that already exists.
                        _("Error renaming file: '%s' to '%s', Filename already exists")
                        % (old_name, new_name + file_extension),
                        "is-danger",
                    )
                else:
                    try:
                        k.song_manager.rename(old_name, new_name)
                    except OSError as e:
                        logging.error(f"Error renaming file: {e}")
                        flash(
                            _("Error renaming file: '%s' to '%s', %s") % (old_name, new_name, e),
                            "is-danger",
                        )
                    else:
                        flash(
                            # MSG: Message shown after renaming a file.
                            _("Renamed file: %s to %s") % (old_name, new_name),
                            "is-warning",
                        )
        else:
            # MSG: Message shown after trying to edit a song without specifying the filename.
            flash(_("Error: No filename parameters were specified!"), "is-danger")
        return redirect(referrer)
