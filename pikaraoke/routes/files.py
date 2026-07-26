"""File management routes for browsing, editing, and deleting songs."""

from __future__ import annotations

import logging
import os

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_paginate import Pagination, get_page_parameter
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

# flask_paginate builds each page link with href.format(page), so the href has to
# reach it holding a literal "{0}". url_for percent-encodes braces, so the page
# number travels through it as this token instead: unreserved characters that come
# back out untouched.
_PAGE_TOKEN = "PIKARAOKE-PAGE-TOKEN"


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
    q = (request.args.get("q") or "").strip()
    page = int(request.args.get("page", 1))
    letter = request.args.get("letter")

    # A text query and a letter jump are two different intents, so q wins.
    if q:
        available_songs = k.song_manager.search(q)
    elif letter:
        available_songs = k.song_manager.songs_by_letter(letter)
    else:
        available_songs = k.song_manager.songs

    # Filtering stays above the date sort so getmtime() only stats the filtered
    # songs -- on a USB or SMB mounted library that is the difference between
    # ~50 stats and ~2000, and gevent does not yield during filesystem I/O.
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
    # Without this the fragment renders pagination links carrying partial=1, so
    # clicking page 2 inside a swapped result table lands on a bare <table> with
    # no nav or stylesheet. The same leak poisons current_url, which becomes the
    # edit button's referrer.
    args.pop("partial", None)

    current_url = url_for("files.browse", **args.to_dict())

    page_param = get_page_parameter()
    args[page_param] = _PAGE_TOKEN

    # Only this one parameter is un-escaped. Unquoting the whole URL instead would
    # decode the user's query too: "Simon & Garfunkel" would split into separate
    # parameters at the "&", and everything after a "#" would become a fragment the
    # server never sees, pinning pagination to page 1.
    pagination_href = url_for("files.browse", **args.to_dict()).replace(
        f"{page_param}={_PAGE_TOKEN}", f"{page_param}={{0}}"
    )

    # `songs` is already filtered, so the filtered count is the only count there is.
    # flask_paginate's search mode exists to report a `found` subset of a larger
    # `total`; leaving it off keeps one number driving both the page links and the
    # message. Turning it on without a `found` reports zero songs and no page links.
    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(songs),
        record_name="songs",
        per_page=results_per_page,
        display_msg="Showing <b>{start} - {end}</b> of <b>{total}</b> {record_name}",
        href=pagination_href,
    )
    start_index = (page - 1) * results_per_page
    context = {
        "pagination": pagination,
        "sort_order": sort_order,
        "site_title": site_name,
        "letter": letter,
        "q": q,
        # MSG: Title of the page listing the songs already on this machine.
        "title": _("Songs"),
        "songs": songs[start_index : start_index + results_per_page],
        "admin": is_admin(),
        "current_url": current_url,
    }
    # Filter keystrokes ask for the result table alone. Rendering base.html per
    # keystroke is pure Python, so on a Pi it blocks the event loop as surely as
    # it wastes bytes over the hotspot. Built from one context dict so the two
    # paths cannot drift.
    if request.args.get("partial"):
        return render_template("partials/browse_results.html", **context)
    return render_template("files.html", **context)


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
