"""Upload local karaoke files into the library."""

from __future__ import annotations

import logging
import os
import zipfile

import flask_babel
from flask import jsonify, render_template, request
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.metadata_parser import sanitize_filename

_ = flask_babel.gettext

upload_bp = Blueprint("upload", __name__)

# Self-contained files only. A loose .mp3/.cdg is half a song; a zipped CDG carries both.
ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".zip"}

# Ceiling on what a zip may expand to at playback. A crafted zip is tiny on disk
# yet unpacks to fill it; read from the central directory, so nothing decompresses.
_MAX_ZIP_UNPACKED_BYTES = 1024 * 1024 * 1024


def _unique_target(directory: str, stem: str, ext: str) -> str:
    """A path in `directory` for `stem+ext` that does not overwrite an existing file."""
    candidate = os.path.join(directory, stem + ext)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem} ({counter}){ext}")
        counter += 1
    return candidate


def _zip_rejection(stream) -> str | None:
    """A reason the player could not use this zip, or None if it can.

    Mirrors file_resolver.handle_zipped_cdg: a top-level .mp3 and .cdg sharing a
    stem. Also caps the unpacked size, which that playback path does not.
    """
    try:
        with zipfile.ZipFile(stream) as zf:
            entries = [i for i in zf.infolist() if "/" not in i.filename and "\\" not in i.filename]
            unpacked = sum(i.file_size for i in entries)
    except (zipfile.BadZipFile, OSError):
        # MSG: Error on an uploaded zip that is not a readable archive.
        return _("Not a valid zip file")
    finally:
        stream.seek(0)

    if unpacked > _MAX_ZIP_UNPACKED_BYTES:
        # MSG: Error on an uploaded zip whose contents would be too large once unpacked.
        return _("Zip contents are too large")
    mp3 = cdg = None
    for entry in entries:
        entry_stem, entry_ext = os.path.splitext(entry.filename)
        if entry_ext.casefold() == ".mp3":
            mp3 = entry_stem
        elif entry_ext.casefold() == ".cdg":
            cdg = entry_stem
    if mp3 is None or mp3 != cdg:
        # MSG: Error on an uploaded zip that is not a matching CDG+MP3 karaoke package.
        return _("Zip must contain a matching CDG and MP3")
    return None


@upload_bp.route("/upload", methods=["GET"])
def upload_page():
    """The drag-and-drop upload page."""
    return render_template(
        "upload.html",
        site_title=get_site_name(),
        # MSG: Title of the page for uploading karaoke files from this device.
        title=_("Upload Files"),
        allowed_extensions=sorted(ALLOWED_UPLOAD_EXTENSIONS),
    )


@upload_bp.route("/api/upload", methods=["POST"])
def upload():
    """Save each uploaded file into the library and announce it to browsers.

    One JSON row per file so a bad file is skipped, not the whole batch.
    """
    k = get_karaoke_instance()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    results = []
    for storage in files:
        name = storage.filename or ""
        stem, ext = os.path.splitext(os.path.basename(name))
        ext = ext.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            # MSG: Error on an uploaded file whose type the library does not accept.
            results.append({"name": name, "ok": False, "error": _("Unsupported file type")})
            continue
        safe_stem = sanitize_filename(stem)
        if not safe_stem:
            # MSG: Error on an uploaded file whose name is empty once illegal characters go.
            results.append({"name": name, "ok": False, "error": _("Invalid file name")})
            continue
        if ext == ".zip":
            reason = _zip_rejection(storage.stream)
            if reason:
                results.append({"name": name, "ok": False, "error": reason})
                continue

        target = _unique_target(k.download_path, safe_stem, ext)
        try:
            storage.save(target)
        except OSError as e:
            logging.error(f"Error saving uploaded file '{name}': {e}")
            # MSG: Error shown when an uploaded file could not be written to disk.
            results.append({"name": name, "ok": False, "error": _("Could not save file")})
            continue

        k.song_manager.register_song(target)
        # Null id: a local file has no YouTube provenance. Browsers refresh as on a download.
        if k.socketio:
            k.socketio.emit("song_downloaded", {"youtube_id": None, "path": target}, namespace="/")
        results.append({"name": name, "ok": True})

    return jsonify({"results": results})
