import os
import re
import time

import flask_babel
from flask import Blueprint, Response, flash, redirect, request, send_file, url_for

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.file_resolver import get_tmp_dir

_ = flask_babel.gettext

stream_bp = Blueprint("stream", __name__)


# Serves HLS playlist file - explicit .m3u8 extension
@stream_bp.route("/stream/<id>.m3u8")
def stream_playlist(id):
    file_path = os.path.join(get_tmp_dir(), f"{id}.m3u8")
    k = get_karaoke_instance()

    # Wait for playlist file to exist
    max_wait = 50  # 5 seconds max
    wait_count = 0
    while not os.path.exists(file_path) and wait_count < max_wait:
        time.sleep(0.1)
        wait_count += 1

    if os.path.exists(file_path):
        return send_file(file_path, mimetype="application/vnd.apple.mpegurl")
    else:
        return Response("Playlist not found", status=404)


# Serves HLS segment files - explicit .ts extension
@stream_bp.route("/stream/<filename>.ts")
def stream_segment(filename):
    # Security: prevent directory traversal
    if '..' in filename or '/' in filename:
        return Response("Invalid segment", status=400)

    segment_path = os.path.join(get_tmp_dir(), f"{filename}.ts")

    if os.path.exists(segment_path):
        return send_file(segment_path, mimetype="video/mp2t")
    else:
        return Response(f"Segment not found: {filename}.ts", status=404)


# Legacy route for backward compatibility (old MP4 streaming)
@stream_bp.route("/stream/<id>")
def stream_legacy(id):
    # Check if it's an HLS request
    if request.path.endswith('.m3u8'):
        return stream_playlist(id.replace('.m3u8', ''))
    elif request.path.endswith('.ts'):
        return stream_segment(id.replace('.ts', ''))
    else:
        # Old MP4 streaming (will be deprecated)
        return Response("Use .m3u8 for HLS streaming", status=404)


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
