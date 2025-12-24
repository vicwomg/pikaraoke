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


# Serves HLS segment files - .m4s (fragmented MP4) extension
@stream_bp.route("/stream/<filename>.m4s")
def stream_segment_m4s(filename):
    # Security: prevent directory traversal
    if '..' in filename or '/' in filename:
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
    if '..' in filename or '/' in filename:
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
    if '..' in filename or '/' in filename:
        return Response("Invalid segment", status=400)

    segment_path = os.path.join(get_tmp_dir(), f"{filename}.ts")

    if os.path.exists(segment_path):
        return send_file(segment_path, mimetype="video/mp2t")
    else:
        return Response(f"Segment not found: {filename}.ts", status=404)


# Smart auto-detection route - serves continuous MP4 to RPi, HLS to Smart TVs
# Supports both /stream/auto/<id> and /stream/auto/<id>.m3u8
@stream_bp.route("/stream/auto/<id>")
@stream_bp.route("/stream/auto/<id>.m3u8")
def stream_auto(id):
    # Remove .m3u8 extension if present
    id = id.replace('.m3u8', '')

    user_agent = request.headers.get('User-Agent', '').lower()
    tmp_dir = get_tmp_dir()

    # Detection: Chrome/Chromium browsers don't support HLS natively
    # Serve progressive MP4 to Chrome (Windows + RPi), HLS to Safari/Smart TVs
    # Note: Chrome user agent includes "Safari" string, so we only check for "chrome"
    is_chrome = "chrome" in user_agent and "smart-tv" not in user_agent

    if is_chrome:
        # Serve continuous MP4 stream for RPi hardware acceleration
        def generate_mp4_stream():
            init_path = os.path.join(tmp_dir, f"{id}_init.mp4")
            # Wait for init file
            max_wait = 100
            wait_count = 0
            while not os.path.exists(init_path) and wait_count < max_wait:
                time.sleep(0.1)
                wait_count += 1

            if os.path.exists(init_path):
                with open(init_path, "rb") as f:
                    yield f.read()

            # Stream segments as they become available
            seg_idx = 0
            max_empty_checks = 50
            empty_checks = 0
            while empty_checks < max_empty_checks:
                seg_path = os.path.join(tmp_dir, f"{id}_segment_{seg_idx:03d}.m4s")
                if os.path.exists(seg_path):
                    with open(seg_path, "rb") as f:
                        yield f.read()
                    seg_idx += 1
                    empty_checks = 0
                else:
                    time.sleep(0.1)
                    empty_checks += 1

        return Response(generate_mp4_stream(), mimetype='video/mp4')
    else:
        # Serve standard HLS for Smart TVs
        playlist_path = os.path.join(tmp_dir, f"{id}.m3u8")
        # Wait for playlist
        max_wait = 50
        wait_count = 0
        while not os.path.exists(playlist_path) and wait_count < max_wait:
            time.sleep(0.1)
            wait_count += 1

        if os.path.exists(playlist_path):
            return send_file(playlist_path, mimetype="application/vnd.apple.mpegurl")
        else:
            return Response("Playlist not found", status=404)


# Main streaming route - serves HLS or progressive MP4 based on file extension
@stream_bp.route("/stream/<id>")
def stream_main(id):
    # Check if it's an HLS request (.m3u8) or MP4 request (.mp4)
    if request.path.endswith('.m3u8'):
        return stream_playlist(id.replace('.m3u8', ''))
    elif request.path.endswith('.mp4'):
        return stream_progressive_mp4(id.replace('.mp4', ''))
    else:
        # Fallback: try HLS first
        return stream_playlist(id)


# Progressive MP4 streaming with chunking (for older RPi)
@stream_bp.route("/stream/<id>.mp4")
def stream_progressive_mp4(id):
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    k = get_karaoke_instance()

    # Wait for file to exist
    max_wait = 50
    wait_count = 0
    while not os.path.exists(file_path) and wait_count < max_wait:
        time.sleep(0.1)
        wait_count += 1

    if not os.path.exists(file_path):
        return Response("MP4 file not found", status=404)

    # Stream with chunking (PR#573 optimization for RPi)
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
