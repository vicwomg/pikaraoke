import os
import re
import time

import flask_babel
from flask import Blueprint, Response, flash, redirect, request, send_file, url_for

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.file_resolver import get_tmp_dir

_ = flask_babel.gettext

stream_bp = Blueprint("stream", __name__)


# Streams the file in chunks from the filesystem (chrome supports it, safari does not)
@stream_bp.route("/stream/<id>")
def stream(id):
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    k = get_karaoke_instance()

    def generate():
        chunk_size = 1024 * 1024 * 2  # 2MB chunks (reduced from 25MB for Raspberry Pi)
        with open(file_path, "rb") as file:
            # Keep yielding file chunks as long as ffmpeg process is transcoding
            while k.ffmpeg_process.poll() is None:
                chunk = file.read(chunk_size)
                if chunk:
                    yield chunk
                else:
                    # No data available yet, wait briefly for ffmpeg to produce more
                    time.sleep(0.05)
            # Read any remaining data after transcoding completes
            while True:
                chunk = file.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    return Response(generate(), mimetype="video/mp4")


def stream_file_path_full(file_path):
    try:
        file_size = os.path.getsize(file_path)
        range_header = request.headers.get("Range", None)
        if not range_header:
            with open(file_path, "rb") as file:
                file_content = file.read()
            return Response(file_content, mimetype="video/mp4")
        # Extract range start and end from Range header (e.g., "bytes=0-499")
        range_match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        start, end = range_match.groups()
        start = int(start)
        end = int(end) if end else file_size - 1
        # Generate response with part of file
        with open(file_path, "rb") as file:
            file.seek(start)
            data = file.read(end - start + 1)
        status_code = 206  # Partial content
        headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(len(data)),
        }
        return Response(data, status=status_code, headers=headers)
    except IOError:
        # MSG: Message shown after trying to stream a file that does not exist.
        flash(_("File not found."), "is-danger")
        return redirect(url_for("home.home"))


# Streams the file in full with proper range headers
# (Safari compatible, but requires the ffmpeg transcoding to be complete to know file size)
@stream_bp.route("/stream/full/<id>")
def stream_full(id):
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    return stream_file_path_full(file_path)


@stream_bp.route("/stream/bg_video")
def stream_bg_video():
    k = get_karaoke_instance()
    file_path = k.bg_video_path
    if k.bg_video_path is not None:
        return send_file(file_path, mimetype="video/mp4")
    else:
        return Response("Background video not found.", status=404)
