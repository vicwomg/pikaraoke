"""Video streaming routes for transcoded media playback."""

import os
import re
import time

import flask_babel
from flask import Blueprint, Response, flash, redirect, request, send_file, url_for

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.file_resolver import get_tmp_dir

_ = flask_babel.gettext

stream_bp = Blueprint("stream", __name__)


@stream_bp.route("/stream/<id>")
def stream(id):
    """Stream transcoded video in chunks (Chrome compatible).
    ---
    tags:
      - Stream
    parameters:
      - name: id
        in: path
        type: string
        required: true
        description: Video stream ID
    produces:
      - video/mp4
    responses:
      200:
        description: Chunked video stream
    """
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    k = get_karaoke_instance()

    def generate():
        position = 0  # Initialize the position variable
        chunk_size = 10240 * 1000 * 25  # Read file in up to 25MB chunks
        with open(file_path, "rb") as file:
            # Keep yielding file chunks as long as ffmpeg process is transcoding
            while k.ffmpeg_process.poll() is None:
                file.seek(position)  # Move to the last read position
                chunk = file.read(chunk_size)
                if chunk is not None and len(chunk) > 0:
                    yield chunk
                    position += len(chunk)  # Update the position with the size of the chunk
                time.sleep(1)  # Wait a bit before checking the file size again
            chunk = file.read(chunk_size)  # Read the last chunk
            yield chunk
            position += len(chunk)  # Update the position with the size of the chunk

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


@stream_bp.route("/stream/full/<id>")
def stream_full(id):
    """Stream video with range headers (Safari compatible).
    ---
    tags:
      - Stream
    parameters:
      - name: id
        in: path
        type: string
        required: true
        description: Video stream ID
    produces:
      - video/mp4
    responses:
      200:
        description: Full video file
      206:
        description: Partial video content (range request)
    """
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    return stream_file_path_full(file_path)


@stream_bp.route("/stream/bg_video")
def stream_bg_video():
    """Stream the background video file.
    ---
    tags:
      - Stream
    produces:
      - video/mp4
    responses:
      200:
        description: Background video file
      404:
        description: Background video not configured
    """
    k = get_karaoke_instance()
    file_path = k.bg_video_path
    if k.bg_video_path is not None:
        return send_file(file_path, mimetype="video/mp4")
    else:
        return Response("Background video not found.", status=404)
