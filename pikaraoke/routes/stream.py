"""Video streaming routes for transcoded media playback."""

import os
import re
import time

import flask_babel
from flask import (
    Blueprint,
    Response,
    flash,
    make_response,
    redirect,
    request,
    send_file,
    url_for,
)

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.file_resolver import FileResolver, get_tmp_dir

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
        # Read file content and return with no-cache headers
        # This is critical for iOS Safari which aggressively caches playlists
        with open(file_path, "r") as f:
            content = f.read()
        response = make_response(content)
        response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    else:
        return Response("Playlist not found", status=404)


# Serves HLS segment files - .m4s (fragmented MP4) extension
@stream_bp.route("/stream/<filename>.m4s")
def stream_segment_m4s(filename):
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return Response("Invalid segment", status=400)

    segment_path = os.path.join(get_tmp_dir(), f"{filename}.m4s")

    if os.path.exists(segment_path):
        return send_file(segment_path, mimetype="video/mp4")
    else:
        return Response(f"Segment not found: {filename}.m4s", status=404)


# Serves init.mp4 header file for fMP4 (with unique filenames per stream)
@stream_bp.route("/stream/<filename>_init.mp4")
def stream_init(filename):
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return Response("Invalid init file", status=400)

    init_path = os.path.join(get_tmp_dir(), f"{filename}_init.mp4")
    if os.path.exists(init_path):
        return send_file(init_path, mimetype="video/mp4")
    else:
        return Response("Init file not found", status=404)


# Legacy .ts support for backward compatibility
@stream_bp.route("/stream/<filename>.ts")
def stream_segment(filename):
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return Response("Invalid segment", status=400)

    segment_path = os.path.join(get_tmp_dir(), f"{filename}.ts")

    if os.path.exists(segment_path):
        return send_file(segment_path, mimetype="video/mp2t")
    else:
        return Response(f"Segment not found: {filename}.ts", status=404)


# Main streaming route - serves HLS or progressive MP4 based on file extension
@stream_bp.route("/stream/<id>")
def stream_main(id):
    # Check if it's an HLS request (.m3u8) or MP4 request (.mp4)
    if request.path.endswith(".m3u8"):
        return stream_playlist(id.replace(".m3u8", ""))
    elif request.path.endswith(".mp4"):
        return stream_progressive_mp4(id.replace(".mp4", ""))
    else:
        # Fallback: try HLS first
        return stream_playlist(id)


# Progressive MP4 streaming with init.mp4 + segments concatenation
# This method works with HLS-generated fMP4 segments but serves them as continuous MP4
# Compatible with Chrome, Firefox and RPi with hardware acceleration
@stream_bp.route("/stream/<id>.mp4")
def stream_progressive_mp4(id):
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    k = get_karaoke_instance()

    def generate():
        position = 0  # Initialize the position variable
        chunk_size = 10240 * 1000 * 25  # Read file in up to 25MB chunks
        with open(file_path, "rb") as file:
            # Keep yielding file chunks as long as ffmpeg process is transcoding
            while k.stream_manager.ffmpeg_process.poll() is None:
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


# Streams the file in full with proper range headers
# (Safari compatible, but requires the ffmpeg transcoding to be complete to know file size)
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


# subtitle .ass
@stream_bp.route("/subtitle/<id>")
def stream_subtitle(id):
    k = get_karaoke_instance()
    try:
        original_file_path = k.now_playing_filename
        if original_file_path and k.now_playing_url and id in k.now_playing_url:
            fr = FileResolver(original_file_path)
            ass_file_path = fr.ass_file_path
            if ass_file_path and os.path.exists(ass_file_path):
                return send_file(
                    ass_file_path,
                    mimetype="text/plain",
                    as_attachment=False,
                    download_name=os.path.basename(ass_file_path),
                )
    except Exception as e:
        k.log_and_send(_("Failed to stream subtitle: ") + str(e), "danger")
        return Response("Subtitle streaming error.", status=500)
    return Response("Subtitle file not found for this stream ID.", status=404)
