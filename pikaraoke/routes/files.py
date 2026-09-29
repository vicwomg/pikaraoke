"""File management routes for browsing, editing, and deleting songs."""

from __future__ import annotations

import logging
import math
import os

import flask_babel
from flask import flash, redirect, render_template, request, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.constants import ITUNES_COUNTRIES, per_page_options
from pikaraoke.karaoke import SongInUseError
from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.metadata_parser import youtube_id_suffix
from pikaraoke.lib.song_manager import rename_collides

_ = flask_babel.gettext

# DB format values that have a matching icon in static/images/formats/
_FORMAT_ICONS = {"mp4", "avi", "mkv", "mov", "webm", "cdg", "ass"}
# Zipped CDG+MP3 packages are stored as "zip" in the DB but use the CDG icon
_FORMAT_ALIASES = {"zip": "cdg"}

# On the device, not in config.ini: one server serves every phone in the room,
# so a browsing choice written server-side moves everyone's default.
_PER_PAGE_COOKIE = "browse_per_page"


def _results_per_page(server_default: int, options: list[int]) -> int:
    """This device's page size, falling back to the server-wide default.

    Sizes off the menu are ignored: the cookie is user-editable, and a hand-set
    50000 would render every row on a Pi that is also decoding video.
    """
    try:
        chosen = int(request.cookies.get(_PER_PAGE_COOKIE, ""))
    except ValueError:
        return server_default
    return chosen if chosen in options else server_default


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


def _build_breadcrumbs(folder: str) -> list[dict[str, str]]:
    """Trail of {name, folder} entries for each segment of a folder key."""
    if not folder:
        return []
    parts = folder.split("/")
    return [{"name": part, "folder": "/".join(parts[: i + 1])} for i, part in enumerate(parts)]


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
@public
def browse():
    """Browse available songs page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    q = (request.args.get("q") or "").strip()
    letter = request.args.get("letter")

    # `view=folders` scopes the listing to one directory, so the folder is a scope
    # and q/letter are filters within it. An unknown folder -- including a "../"
    # traversal attempt -- is not a key in the tree, so it clamps to the root
    # rather than being validated against the filesystem.
    #
    # Folder browsing is opt-in. The empty tree stands in when it is off, which
    # drops the toggle and makes a bookmarked ?view=folders fall back to the flat
    # list rather than 404 -- the preference can be turned off while a phone is
    # sitting on a folder page.
    folder_tree = k.song_manager.folder_tree() if k.enable_folder_browsing else {"": []}
    has_subfolders = bool(folder_tree[""])
    in_folders = has_subfolders and request.args.get("view") == "folders"
    folder = request.args.get("folder", "") if in_folders else ""
    if folder not in folder_tree:
        folder = ""
    scope = folder if in_folders else None

    # A text query and a letter jump are two different intents, so q wins.
    if q:
        available_songs = k.song_manager.search(q, folder=scope)
    elif letter:
        available_songs = k.song_manager.songs_by_letter(letter, folder=scope)
    elif in_folders:
        available_songs = k.song_manager.songs_in_folder(folder)
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

    size_options = per_page_options(k.browse_results_per_page)
    results_per_page = _results_per_page(k.browse_results_per_page, size_options)
    # `songs` is already filtered, so the filtered count is the only count there is.
    total = len(songs)
    # Resolved before the URLs below are built, so the pager links and the edit
    # referrer describe the page that was rendered rather than the one asked for.
    # Junk, a zero and a page past the end all land somewhere real: the pager links
    # to the page either side of this one, and a bookmark is not worth a 500.
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    page = min(max(page, 1), max(1, math.ceil(total / results_per_page)))
    start_index = (page - 1) * results_per_page

    args = request.args.copy()
    args.pop("_", None)
    # Without this the fragment renders pagination links carrying partial=1, so
    # clicking page 2 inside a swapped result table lands on a bare <table> with
    # no nav or stylesheet. The same leak poisons current_url, which becomes the
    # edit button's referrer.
    args.pop("partial", None)
    # Rewritten rather than passed through, because the clamps above may have
    # dropped a folder or a page the request asked for. These args build the
    # pagination links and the edit referrer, so they have to describe what was
    # rendered -- an edit started from a clamped page comes back to a real one.
    args.pop("view", None)
    args.pop("folder", None)
    if in_folders:
        args["view"] = "folders"
        if folder:
            args["folder"] = folder
    args["page"] = page

    current_url = url_for("files.browse", **args.to_dict())

    # Substituted into the rendered URL rather than appended, which would have to
    # guess whether the separator is "?" or "&". Unreserved characters, so url_for's
    # percent-encoding hands it back untouched; partials/pager.html replaces it.
    args["page"] = "PAGENUMBER"
    page_href = url_for("files.browse", **args.to_dict())

    admin = is_admin()
    context = {
        "page": page,
        "per_page": results_per_page,
        # Everyone, not just the host: the size is per-device now.
        "per_page_options": size_options,
        "total": total,
        "skip": start_index,
        "page_href": page_href,
        "sort_order": sort_order,
        "site_title": site_name,
        "letter": letter,
        # Scoped, not filtered: the alpha bar sits outside the swapped fragment, so
        # which letters it offers must not move as the user types.
        "letters": k.song_manager.letters_with_songs(scope),
        "q": q,
        # MSG: Title of the page listing the songs already on this machine.
        "title": _("Songs"),
        "songs": songs[start_index : start_index + results_per_page],
        "admin": admin,
        "current_url": current_url,
        "view": "folders" if in_folders else "",
        "folder": folder,
        "subfolders": folder_tree[folder] if in_folders else [],
        "breadcrumbs": _build_breadcrumbs(folder),
        # Drives whether the folder toggle is offered at all: a flat library never
        # sees it, so nothing about the page changes for those users.
        "has_subfolders": has_subfolders,
    }
    # Filter keystrokes ask for the result table alone. Rendering base.html per
    # keystroke is pure Python, so on a Pi it blocks the event loop as surely as
    # it wastes bytes over the hotspot. Built from one context dict so the two
    # paths cannot drift.
    if request.args.get("partial"):
        return render_template("partials/browse_results.html", **context)
    return render_template("files.html", **context)


@files_bp.route("/files/delete", methods=["POST"])
@files_bp.arguments(SongReferrerQuery, location="query")
def delete_file(query):
    """Delete a song file."""
    k = get_karaoke_instance()
    song_path = query["song"]
    referrer = query.get("referrer") or url_for("files.browse")
    if k.is_song_in_use(song_path):
        flash(
            # MSG: Message shown after trying to delete a song that is queued or playing.
            _("Error: Can't delete this song because it is queued or playing") + ": " + song_path,
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


def _render_edit_page(k, song_path: str, new_name: str, referrer: str, error: str | None = None):
    """Render the rename page with `new_name` in the input and `error` above it."""
    return render_template(
        "edit.html",
        site_title=get_site_name(),
        # MSG: Title of the page where a song can be renamed.
        title=_("Rename Song"),
        song=song_path,
        # What is on disk, which on a failed save is precisely what did not change.
        raw_stem=k.song_manager.filename_from_path(song_path, tidy=False),
        new_name=new_name,
        format_icon=_format_icon(song_path, k.db.get_format(song_path)),
        referrer=referrer,
        itunes_countries=ITUNES_COUNTRIES,
        itunes_search_country=k.preferences.get_or_default("itunes_search_country"),
        error=error,
    )


@files_bp.route("/files/edit", methods=["GET"])
@files_bp.arguments(SongReferrerQuery, location="query")
def edit_file(query):
    """Show the song rename page."""
    k = get_karaoke_instance()
    song_path = query["song"]
    referrer = query.get("referrer") or url_for("files.browse")
    if k.playback_controller.now_playing_filename == song_path:
        # MSG: Message shown after trying to rename the song that is playing.
        flash(_("This song is playing. Rename it when it finishes."), "is-danger")
        return redirect(referrer)
    raw_stem = k.song_manager.filename_from_path(song_path, tidy=False)
    return _render_edit_page(k, song_path, raw_stem, referrer)


@files_bp.route("/files/edit", methods=["POST"])
@files_bp.arguments(EditFileForm, location="form")
def rename_file(form):
    """Process a song rename.

    Redirects to the referrer only when the file actually moved; every refusal
    re-renders the page with the submitted name, so nothing typed is lost.
    """
    k = get_karaoke_instance()
    referrer = form.get("referrer") or url_for("files.browse")
    new_name = form["new_file_name"]
    old_name = form["old_file_name"]

    if not new_name.strip():
        # MSG: Message shown after saving the rename page with an empty name.
        error = _("Enter a name for this song.")
        return _render_edit_page(k, old_name, new_name, referrer, error)

    if not os.path.isfile(old_name):
        # MSG: Message shown when the song being renamed is gone from the library.
        error = _(
            "This song is no longer in the library. "
            "It may have been renamed or deleted from another device."
        )
        return _render_edit_page(k, old_name, new_name, referrer, error)

    new_name_full = new_name + youtube_id_suffix(old_name)
    target = k.song_manager.rename_target(old_name, new_name_full)
    # A song already named what you asked for is done, not a collision with itself.
    if target == old_name:
        return redirect(referrer)
    if rename_collides(old_name, target):
        # MSG: Message shown after trying to rename a song to a name already in use.
        error = _("A song called '%s' already exists.") % os.path.basename(target)
        return _render_edit_page(k, old_name, new_name, referrer, error)

    try:
        k.rename_song(old_name, new_name_full)
    except SongInUseError:
        # MSG: Message shown when the song being renamed started playing mid-edit.
        error = _(
            "This song started playing while you were editing it. Rename it when it finishes."
        )
        return _render_edit_page(k, old_name, new_name, referrer, error)
    except OSError as e:
        logging.error(f"Error renaming file: {e}")
        # MSG: Message shown after a rename failed. Followed by the system error.
        error = _("Error renaming file: %s") % e
        return _render_edit_page(k, old_name, new_name, referrer, error)

    flash(
        # MSG: Message shown after renaming a file.
        _("Renamed file: %s to %s") % (old_name, new_name_full),
        "is-warning",
    )
    return redirect(referrer)
