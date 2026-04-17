"""Video streaming routes for transcoded media playback."""

import os
import re
import time

import flask_babel
from flask import Response, make_response, request, send_file, stream_with_context
from flask_smorest import Blueprint

_ = flask_babel.gettext

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.file_resolver import FileResolver, get_tmp_dir

stream_bp = Blueprint("stream", __name__)


def _wait_for_file(path: str, max_wait_tenths: int = 50) -> bool:
    """Poll for a file to exist, up to ~max_wait_tenths/10 seconds.

    hls.js requests segment N+1 the moment segment N plays; ffmpeg may still
    be encoding it. Without this wait, a 404 here is fatal — hls.js stalls.
    """
    wait_count = 0
    while not os.path.exists(path) and wait_count < max_wait_tenths:
        time.sleep(0.1)
        wait_count += 1
    return os.path.exists(path)


def _wait_for_m3u8_ready(path: str, min_segments: int = 2, max_wait_tenths: int = 50) -> bool:
    """Wait for HLS playlist to exist and reference at least min_segments.

    Serving an m3u8 with only a single segment and no ENDLIST is fragile:
    hls.js plays the one segment, then the buffer drains before it has
    polled for playlist updates. Waiting for two segments (or ENDLIST)
    ensures there's always enough buffered content for hls.js to carry
    on while fetching more.
    """
    wait_count = 0
    while wait_count < max_wait_tenths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    content = f.read()
                if "#EXT-X-ENDLIST" in content or content.count("#EXTINF:") >= min_segments:
                    return True
            except OSError:
                pass
        time.sleep(0.1)
        wait_count += 1
    return os.path.exists(path)


@stream_bp.route("/stream/<stream_id>/<stem>.<ext>")
def stream_stem_audio(stream_id: str, stem: str, ext: str):
    """Tail a stem audio file (vocals/instrumental) for client-side mixing.

    Reads the file in the cache directory that StreamManager has registered
    for this stream. When Demucs is still writing (tier 3), the generator
    sleeps until new bytes are appended, then yields them, stopping when the
    done_event is set. When the file is fully cached (tier 1/2), it streams
    normally and exits at EOF.
    """
    if stem not in ("vocals", "instrumental") or ext not in ("wav", "mp3"):
        return Response("Invalid stem or format", status=400)

    k = get_karaoke_instance()
    stems = k.playback_controller.stream_manager.active_stems.get(stream_id)
    if stems is None:
        return Response("Stream not active", status=404)

    path = stems.vocals_path if stem == "vocals" else stems.instrumental_path
    # Grace period: live Demucs registers the stream before its bg thread
    # has created the .partial file. Wait up to ~15s for it to appear.
    if path and not os.path.exists(path):
        for _ in range(150):
            time.sleep(0.1)
            if os.path.exists(path):
                break
            # Path may have been swapped to the final .wav mid-wait.
            path = stems.vocals_path if stem == "vocals" else stems.instrumental_path
            if os.path.exists(path):
                break
    # Race: encode_mp3_in_background replaces .wav with .mp3 on disk while
    # ActiveStems still points to the .wav. Fall back to the sibling mp3
    # (or vice versa) and update ActiveStems so future fetches find it.
    if path and not os.path.exists(path):
        alt = path[:-4] + ".mp3" if path.endswith(".wav") else path[:-4] + ".wav"
        if os.path.exists(alt):
            path = alt
            if stem == "vocals":
                stems.vocals_path = alt
            else:
                stems.instrumental_path = alt
            stems.format = "mp3" if alt.endswith(".mp3") else "wav"
    if not path or not os.path.exists(path):
        return Response("Stem file missing", status=404)

    done_event = stems.done_event
    # Serve with the mimetype of the file we actually have on disk, not the
    # URL extension — otherwise the fallback above (wav URL -> mp3 file)
    # ships MP3 bytes as audio/wav and some browsers reject it.
    mimetype = "audio/mpeg" if path.endswith(".mp3") else "audio/wav"

    # Fully-written files (cache hit, or Demucs completed mid-song) — serve
    # with range support so the browser can seek via HTTP byte ranges.
    if done_event.is_set() and not path.endswith(".partial"):
        return send_file(path, mimetype=mimetype, conditional=True)

    def generate():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if chunk:
                    yield chunk
                    continue
                if done_event.is_set():
                    # Drain anything appended between our last read and the set
                    tail = f.read()
                    if tail:
                        yield tail
                    return
                time.sleep(0.1)

    response = Response(stream_with_context(generate()), mimetype=mimetype)
    # Live Demucs — file is growing, range requests would be inconsistent.
    response.headers["Accept-Ranges"] = "none"
    response.headers["Cache-Control"] = "no-cache, no-store"
    return response


@stream_bp.route("/stream/audio/<stream_uid>/track.wav")
def stream_audio_track(stream_uid: str):
    """Serve the per-request WAV audio for the direct-video pipeline.

    The generator pipes source audio through ffmpeg (optional rubberband
    pitch + loudnorm) and emits a virtual WAV whose byte length matches
    the song's duration; HTTP Range requests land on integer-second
    ffmpeg seeks and the leading sub-second bytes are discarded inside
    the generator for exact byte fidelity.
    """
    from pikaraoke.lib.audio_processor import stream_wav_range

    k = get_karaoke_instance()
    config = k.playback_controller.stream_manager.active_audio.get(stream_uid)
    if config is None:
        return Response("Audio track not active", status=404)

    generate, status, headers, _total = stream_wav_range(
        config, request.headers.get("Range")
    )
    return Response(stream_with_context(generate()), status=status, headers=headers)


@stream_bp.route("/stream/video/<stream_uid>.mp4")
def stream_source_mp4(stream_uid: str):
    """Serve the original source mp4 with HTTP byte-range seeking.

    Used when the source is a browser-native h264/aac mp4 and no audio
    transforms are required; bypasses the copy-to-tmp+transcode path
    entirely. Source file path is registered by StreamManager.play_file.
    """
    k = get_karaoke_instance()

    if not k.playback_controller.is_playing:
        now_playing_url = k.playback_controller.now_playing_url
        if now_playing_url and stream_uid in now_playing_url:
            k.playback_controller.start_song()

    source_path = k.playback_controller.stream_manager.active_sources.get(stream_uid)
    if not source_path or not os.path.exists(source_path):
        return Response("Stream source not found", status=404)
    return send_file(source_path, mimetype="video/mp4", conditional=True)


# Serves HLS playlist file - explicit .m3u8 extension
@stream_bp.route("/stream/<id>.m3u8")
def stream_playlist(id):
    """Serve HLS playlist file."""
    file_path = os.path.join(get_tmp_dir(), f"{id}.m3u8")
    k = get_karaoke_instance()

    # Mark song as started when client connects (idempotent)
    # Validate stream ID matches current song to prevent stale requests from setting is_playing
    if not k.playback_controller.is_playing:
        now_playing_url = k.playback_controller.now_playing_url
        if now_playing_url and id in now_playing_url:
            k.playback_controller.start_song()

    if _wait_for_m3u8_ready(file_path):
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
    """Serve HLS segment file (fragmented MP4)."""
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return Response("Invalid segment", status=400)

    segment_path = os.path.join(get_tmp_dir(), f"{filename}.m4s")

    if _wait_for_file(segment_path):
        return send_file(segment_path, mimetype="video/mp4")
    return Response(f"Segment not found: {filename}.m4s", status=404)


# Serves init.mp4 header file for fMP4 (with unique filenames per stream)
@stream_bp.route("/stream/<filename>_init.mp4")
def stream_init(filename):
    """Serve init.mp4 header file for fragmented MP4 streams."""
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return Response("Invalid init file", status=400)

    init_path = os.path.join(get_tmp_dir(), f"{filename}_init.mp4")
    if _wait_for_file(init_path):
        return send_file(init_path, mimetype="video/mp4")
    return Response("Init file not found", status=404)


# Legacy .ts support for backward compatibility
@stream_bp.route("/stream/<filename>.ts")
def stream_segment(filename):
    """Serve HLS segment file (MPEG-TS)."""
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return Response("Invalid segment", status=400)

    segment_path = os.path.join(get_tmp_dir(), f"{filename}.ts")

    if _wait_for_file(segment_path):
        return send_file(segment_path, mimetype="video/mp2t")
    return Response(f"Segment not found: {filename}.ts", status=404)


# Main streaming route - serves HLS or progressive MP4 based on file extension
@stream_bp.route("/stream/<id>")
def stream_main(id):
    """Route streaming request to HLS or progressive MP4."""
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
    """Stream progressive MP4 from HLS-generated segments."""
    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    k = get_karaoke_instance()

    # Mark song as started when client connects (idempotent)
    # Validate stream ID matches current song to prevent stale requests from setting is_playing
    if not k.playback_controller.is_playing:
        now_playing_url = k.playback_controller.now_playing_url
        if now_playing_url and id in now_playing_url:
            k.playback_controller.start_song()

    # Wait for output file to exist
    max_wait = 50  # 5 seconds max
    wait_count = 0
    while not os.path.exists(file_path) and wait_count < max_wait:
        time.sleep(0.1)
        wait_count += 1

    if not os.path.exists(file_path):
        return Response("Stream file not ready", status=404)

    def generate():
        position = 0  # Initialize the position variable
        chunk_size = 10240 * 1000 * 25  # Read file in up to 25MB chunks
        with open(file_path, "rb") as file:
            # Keep yielding file chunks as long as ffmpeg process is transcoding
            while k.playback_controller.ffmpeg_process.poll() is None:
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
        return Response("File not found.", status=404)


# Streams the file in full with proper range headers
# (Safari compatible, but requires the ffmpeg transcoding to be complete to know file size)
@stream_bp.route("/stream/full/<id>")
def stream_full(id):
    """Stream video with range headers (Safari compatible)."""
    k = get_karaoke_instance()

    # Mark song as started when client connects (idempotent)
    # Validate stream ID matches current song to prevent stale requests from setting is_playing
    if not k.playback_controller.is_playing:
        now_playing_url = k.playback_controller.now_playing_url
        if now_playing_url and id in now_playing_url:
            k.playback_controller.start_song()

    file_path = os.path.join(get_tmp_dir(), f"{id}.mp4")
    return stream_file_path_full(file_path)


@stream_bp.route("/stream/bg_video")
def stream_bg_video():
    """Stream the background video file."""
    k = get_karaoke_instance()
    file_path = k.bg_video_path
    if k.bg_video_path is not None:
        return send_file(os.path.abspath(file_path), mimetype="video/mp4")
    else:
        return Response("Background video not found.", status=404)


# subtitle .ass
@stream_bp.route("/subtitle/<id>")
def stream_subtitle(id):
    """Serve subtitle file for the current song."""
    k = get_karaoke_instance()
    try:
        original_file_path = k.playback_controller.now_playing_filename
        now_playing_url = k.playback_controller.now_playing_url
        if original_file_path and now_playing_url and id in now_playing_url:
            fr = FileResolver(original_file_path)
            ass_file_path = fr.ass_file_path
            if ass_file_path and os.path.exists(ass_file_path):
                return send_file(
                    os.path.abspath(ass_file_path),
                    mimetype="text/plain",
                    as_attachment=False,
                    download_name=os.path.basename(ass_file_path),
                )
    except Exception as e:
        k.log_and_send(_("Failed to stream subtitle: ") + str(e), "danger")
        return Response("Subtitle streaming error.", status=500)
    return Response("Subtitle file not found for this stream ID.", status=404)
