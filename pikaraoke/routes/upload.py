"""Upload local karaoke files into the library."""

import logging
import os
import zipfile

import flask_babel
from flask import jsonify, render_template, request
from flask_smorest import Blueprint
from werkzeug.datastructures import FileStorage

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.metadata_parser import sanitize_filename

_ = flask_babel.gettext

upload_bp = Blueprint("upload", __name__)

# Video files play on their own; a zipped CDG carries its own graphics.
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
# A primary is the file that becomes the library song.
PRIMARY_EXTENSIONS = VIDEO_EXTENSIONS | {".mp3", ".zip"}
# A companion rides alongside its primary, sharing the stem: .cdg graphics for an
# .mp3, .ass subtitles for a video. The player rediscovers them by stem at playback.
COMPANION_EXTENSIONS = {".cdg", ".ass"}
ALLOWED_UPLOAD_EXTENSIONS = PRIMARY_EXTENSIONS | COMPANION_EXTENSIONS

# Ceiling on what a zip may expand to at playback. A crafted zip is tiny on disk
# yet unpacks to fill it; read from the central directory, so nothing decompresses.
_MAX_ZIP_UNPACKED_BYTES = 1024 * 1024 * 1024


def _unique_target(directory: str, stem: str, primary_ext: str, group_exts: set[str]) -> str:
    """A path for `stem+primary_ext` whose stem is free for every ext in the group.

    The pair shares one stem, so a clash on the companion's `.cdg` overwrites an
    unrelated file as surely as one on the primary.
    """
    candidate_stem = stem
    counter = 2
    while any(os.path.exists(os.path.join(directory, candidate_stem + e)) for e in group_exts):
        candidate_stem = f"{stem} ({counter})"
        counter += 1
    return os.path.join(directory, candidate_stem + primary_ext)


def _zip_rejection(stream) -> str | None:
    """A reason the player could not use this zip, or None if it can.

    Mirrors file_resolver.handle_zipped_cdg: a top-level .mp3 and .cdg sharing a
    stem. Also caps the unpacked size, which that playback path does not.
    """
    try:
        with zipfile.ZipFile(stream) as zf:
            infos = zf.infolist()
            # Sum every entry extractall unpacks, not just the top-level pair.
            unpacked = sum(i.file_size for i in infos)
            entries = [i for i in infos if "/" not in i.filename and "\\" not in i.filename]
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


def _ext_of(storage: FileStorage) -> str:
    """Lowercase extension of an uploaded file's basename."""
    return os.path.splitext(os.path.basename(storage.filename or ""))[1].lower()


def _classify(storages: list[FileStorage]):
    """Split a stem-group into its one primary and its companions.

    Returns (primary, companions, error), where primary is (ext, storage) and
    companions is a list of (ext, storage). On a shape the player cannot open,
    primary is None and error is the reason.
    """
    primaries: list[tuple[str, FileStorage]] = []
    companions: list[tuple[str, FileStorage]] = []
    for storage in storages:
        ext = _ext_of(storage)
        if ext in PRIMARY_EXTENSIONS:
            primaries.append((ext, storage))
        elif ext in COMPANION_EXTENSIONS:
            companions.append((ext, storage))
        else:
            # MSG: Error on an uploaded file whose type the library does not accept.
            return None, [], _("Unsupported file type")

    if not primaries:
        if any(ext == ".cdg" for ext, _s in companions):
            # MSG: Error on an uploaded .cdg with no .mp3 of the same name beside it.
            return None, [], _("A CDG needs its matching MP3")
        # MSG: Error on an uploaded .ass subtitle with no video of the same name beside it.
        return None, [], _("A subtitle needs its matching video")
    if len(primaries) > 1:
        # MSG: Error when two playable files share one name, so neither is clearly the song.
        return None, [], _("Only one song per file name")

    primary_ext, primary = primaries[0]
    for comp_ext, _s in companions:
        if comp_ext == ".cdg" and primary_ext != ".mp3":
            # MSG: Error on a .cdg uploaded next to something other than an .mp3.
            return None, [], _("A CDG goes with an MP3")
        if comp_ext == ".ass" and primary_ext not in VIDEO_EXTENSIONS:
            # MSG: Error on an .ass subtitle uploaded next to something other than a video.
            return None, [], _("A subtitle goes with a video")
    if primary_ext == ".mp3" and not any(ext == ".cdg" for ext, _s in companions):
        # MSG: Error on a lone .mp3, which cannot play without its .cdg graphics.
        return None, [], _("An MP3 needs its matching CDG")
    return (primary_ext, primary), companions, None


def _save_group(k, name: str, storages: list[FileStorage]) -> dict:
    """Save one song and its companions into the library, returning a result row."""
    classification, companions, error = _classify(storages)
    if error:
        return {"name": name, "ok": False, "error": error}

    primary_ext, primary = classification
    stem = os.path.splitext(os.path.basename(primary.filename or ""))[0]
    safe_stem = sanitize_filename(stem)
    if not safe_stem:
        # MSG: Error on an uploaded file whose name is empty once illegal characters go.
        return {"name": name, "ok": False, "error": _("Invalid file name")}
    if primary_ext == ".zip":
        reason = _zip_rejection(primary.stream)
        if reason:
            return {"name": name, "ok": False, "error": reason}

    group_exts = {primary_ext} | {comp_ext for comp_ext, _s in companions}
    target = _unique_target(k.download_path, safe_stem, primary_ext, group_exts)
    try:
        primary.save(target)
        # Companions take the primary's final stem, not their own name: a collision
        # suffix must carry across so the player's stem-match still finds the pair,
        # and the saved name is derived, never the uploaded (untrusted) one.
        final_stem = os.path.splitext(os.path.basename(target))[0]
        for comp_ext, companion in companions:
            companion.save(os.path.join(k.download_path, final_stem + comp_ext))
    except OSError as e:
        logging.error(f"Error saving uploaded file '{name}': {e}")
        # MSG: Error shown when an uploaded file could not be written to disk.
        return {"name": name, "ok": False, "error": _("Could not save file")}

    k.song_manager.register_song(target)
    # Null id: a local file has no YouTube provenance. Browsers refresh as on a download.
    if k.socketio:
        k.socketio.emit("song_downloaded", {"youtube_id": None, "path": target}, namespace="/")
    return {"name": name, "ok": True}


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
    """Save each uploaded song into the library and announce it to browsers.

    Files are grouped by filename stem, so a song arrives with its companion
    (`.mp3`+`.cdg`, or a video and its `.ass`). One JSON row per group so a bad
    song is skipped, not the whole batch.
    """
    k = get_karaoke_instance()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    groups: dict[str, list[FileStorage]] = {}
    order: list[str] = []
    for storage in files:
        stem = os.path.splitext(os.path.basename(storage.filename or ""))[0]
        if stem not in groups:
            groups[stem] = []
            order.append(stem)
        groups[stem].append(storage)

    results = [_save_group(k, groups[stem][0].filename or "", groups[stem]) for stem in order]
    return jsonify({"results": results})
