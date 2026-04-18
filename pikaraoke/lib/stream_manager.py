"""Stream manager for handling video transcoding and playback setup."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Any

from pikaraoke.lib.audio_processor import AudioTrackConfig
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import build_ffmpeg_cmd
from pikaraoke.lib.file_resolver import (
    FileResolver,
    can_serve_directly,
    can_serve_video_directly,
    is_transcoding_required,
)
from pikaraoke.lib.preference_manager import PreferenceManager

# Mirror of hls_time in build_ffmpeg_cmd. Each .m4s segment covers roughly
# this many seconds of source video — close enough for a buffered-seek UI.
HLS_SEGMENT_DURATION = 3.0


@dataclass
class ActiveStems:
    """In-progress or cached stems for the currently playing song.

    Held by StreamManager so the HTTP tail route can locate files by stream_uid.
    """

    vocals_path: str
    instrumental_path: str
    format: str  # "wav" or "mp3"
    done_event: threading.Event  # set when stem files are fully written
    ready_event: threading.Event  # set when the first segment is on disk
    processed_seconds: float = 0.0
    total_seconds: float = 0.0
    # Transform prefs captured at song start. The stem HTTP route pipes the
    # cached file through ffmpeg (rubberband + loudnorm) when either is set.
    # Stashed here so mid-song slider changes don't retroactively mutate the
    # currently-playing mix.
    semitones: int = 0
    normalize: bool = False


@dataclass
class PlaybackResult:
    """Result of a playback operation.

    Attributes:
        success: Whether playback started successfully.
        stream_url: URL path for the video stream.
        subtitle_url: URL path for subtitles (if present).
        audio_track_url: URL path for a separately-served audio track, when
            the video stream is muted and audio is piped from a second
            process (direct-video + on-the-fly transforms).
        avsync_offset_ms: Client-side audio offset in milliseconds. Applied
            as audioElement.currentTime = video.currentTime + offset/1000.
        duration: Video duration in seconds.
        error: Error message if playback failed.
    """

    success: bool
    stream_url: str | None = None
    subtitle_url: str | None = None
    audio_track_url: str | None = None
    avsync_offset_ms: int = 0
    duration: int | None = None
    error: str | None = None


def enqueue_output(out: Any, queue: Queue) -> None:
    """Read lines from a stream and put them in a queue without blocking.

    Args:
        out: File-like object to read from (e.g., subprocess stderr).
        queue: Queue to put the read lines into.
    """
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


class StreamManager:
    """Manages video transcoding and stream preparation for playback.

    Handles FFmpeg transcoding, buffering monitoring, and stream URL setup
    for both HLS and progressive MP4 streaming formats.

    Attributes:
        preferences: PreferenceManager for configuration.
        ffmpeg_process: Currently running FFmpeg subprocess.
        ffmpeg_log: Queue for FFmpeg stderr output.
    """

    def __init__(
        self,
        preferences: PreferenceManager,
        streaming_format: str = "hls",
        events: EventSystem | None = None,
    ) -> None:
        """Initialize the stream manager.

        Args:
            preferences: PreferenceManager instance for configuration.
            streaming_format: Video streaming format ('hls' or 'mp4').
            events: Optional EventSystem to emit 'demucs_progress' events on.
        """
        self.preferences = preferences
        self.streaming_format = streaming_format
        self.events = events
        self.ffmpeg_process = None
        self.ffmpeg_log: Queue | None = None
        # Map of stream_uid -> ActiveStems, for the HTTP tail routes that
        # serve vocals/instrumental audio to the browser.
        self.active_stems: dict[str, ActiveStems] = {}
        # Map of stream_uid -> source file path, for the direct-mp4 route
        # that serves the original file with HTTP byte-range seeking.
        self.active_sources: dict[str, str] = {}
        # Map of stream_uid -> AudioTrackConfig for the on-the-fly audio
        # pipeline route (transforms applied per-request via ffmpeg).
        self.active_audio: dict[str, AudioTrackConfig] = {}

    def play_file(self, file_path: str, semitones: int = 0) -> PlaybackResult:
        """Start playback of a media file.

        Handles file resolution, transcoding, and stream setup.

        Args:
            file_path: Path to the media file to play.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            PlaybackResult with success status and stream information.
        """
        from flask_babel import _

        streaming_format = self.streaming_format
        normalize_audio = self.preferences.get_or_default("normalize_audio")
        avsync = self.preferences.get_or_default("avsync")
        vocal_removal = self.preferences.get_or_default("vocal_removal")
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )

        is_hls = streaming_format == "hls"
        needs_audio_transforms = semitones != 0 or normalize_audio
        # avsync moves to the client on the direct path; server-side filters
        # remain on the HLS fallback (see _transcode_file).
        requires_transcoding = (
            is_transcoding_required(file_path) or is_hls or avsync != 0 or needs_audio_transforms
        )

        logging.debug(f"Requires transcoding (pre-direct check): {requires_transcoding}")

        try:
            fr = FileResolver(file_path, streaming_format)
        except Exception as e:
            error_message = _("Error resolving file: %s") % str(e)
            logging.error(error_message)
            return PlaybackResult(success=False, error=error_message)

        # Demucs runs alongside (not inside) video transcoding. Video uses
        # the original audio track; splash.js crossfades to stems when the
        # `stems_ready` event arrives.
        if vocal_removal and fr.file_path:
            self._prepare_stems(fr)
            entry = self.active_stems.get(str(fr.stream_uid))
            if entry is not None:
                entry.semitones = semitones
                entry.normalize = normalize_audio

        # Direct-video path: h264 mp4 source. If audio needs transforms or
        # is codec-incompatible, spin up a separate audio pipe route;
        # otherwise let the <video> element use its native audio track.
        can_direct_video = (
            not is_hls
            and not is_transcoding_required(file_path)
            and fr.file_path is not None
            and can_serve_video_directly(fr.file_path)
        )

        stream_url_path: str
        audio_track_url: str | None = None
        avsync_offset_ms = 0

        if can_direct_video:
            uid = str(fr.stream_uid)
            stream_url_path = f"/stream/video/{fr.stream_uid}.mp4"
            self.active_sources[uid] = fr.file_path  # type: ignore[assignment]
            has_audio_sibling = fr.audio_sibling_path is not None
            # When vocal_removal is on, stems carry the audio — but only
            # once they land. A cache hit (done_event set in _prepare_stems)
            # means stems start instantly; otherwise we pipe the m4a
            # sibling during Demucs warmup so the user hears the song
            # instead of silence. splash.js crossfades m4a → stems when
            # the `stems_ready` event arrives.
            stems_cache_hit = False
            if vocal_removal:
                entry = self.active_stems.get(uid)
                stems_cache_hit = entry is not None and entry.done_event.is_set()
            if vocal_removal:
                # No warmup fallback for old muxed mp4s — the video track
                # still has audio to play until stems_ready fires.
                needs_audio_pipe = has_audio_sibling and not stems_cache_hit
            else:
                # Direct-video path: transforms, client-side avsync, codec
                # incompatibility, or a silent split-download video all
                # require a separate audio pipe.
                needs_audio_pipe = (
                    has_audio_sibling
                    or needs_audio_transforms
                    or avsync != 0
                    or not can_serve_directly(fr.file_path)
                )
            if needs_audio_pipe:
                audio_source = fr.audio_sibling_path or fr.file_path
                self.active_audio[uid] = AudioTrackConfig(
                    source_path=audio_source,  # type: ignore[arg-type]
                    duration_sec=float(fr.duration or 0),
                    semitones=semitones,
                    normalize=normalize_audio,
                )
                audio_track_url = f"/stream/audio/{fr.stream_uid}/track.wav"
                avsync_offset_ms = int(avsync * 1000)
            is_transcoding_complete = True
            is_buffering_complete = True
        elif is_hls:
            stream_url_path = f"/stream/{fr.stream_uid}.m3u8"
            is_transcoding_complete, is_buffering_complete = self._transcode_file(
                fr, semitones, is_hls
            )
        elif not requires_transcoding:
            stream_url_path = f"/stream/full/{fr.stream_uid}"
            is_transcoding_complete = self._copy_file(file_path, fr.output_file)
            is_buffering_complete = True
        else:
            if complete_transcode_before_play:
                stream_url_path = f"/stream/full/{fr.stream_uid}"
            else:
                stream_url_path = f"/stream/{fr.stream_uid}.mp4"
            is_transcoding_complete, is_buffering_complete = self._transcode_file(
                fr, semitones, is_hls
            )

        subtitle_url = None
        if fr.ass_file_path:
            subtitle_url = f"/subtitle/{fr.stream_uid}"
            logging.debug(f"Subtitle file found: {fr.ass_file_path}. URL: {subtitle_url}")

        # Check if the stream is ready to play
        if is_transcoding_complete or is_buffering_complete:
            logging.debug("Stream ready!")
            return PlaybackResult(
                success=True,
                stream_url=stream_url_path,
                subtitle_url=subtitle_url,
                audio_track_url=audio_track_url,
                avsync_offset_ms=avsync_offset_ms,
                duration=fr.duration,
            )
        else:
            error_message = _("Failed to prepare stream")
            logging.error(error_message)
            return PlaybackResult(success=False, error=error_message)

    def _copy_file(self, src_path: str, dest_path: str) -> bool:
        """Copy a file that doesn't need transcoding.

        Args:
            src_path: Source file path.
            dest_path: Destination file path.

        Returns:
            True if copy succeeded, False otherwise.
        """
        shutil.copy(src_path, dest_path)
        max_retries = 5
        while max_retries > 0:
            if os.path.exists(dest_path):
                return True
            max_retries -= 1
            time.sleep(1)
        logging.debug(f"Copying file failed: {dest_path}")
        return False

    def _transcode_file(self, fr: FileResolver, semitones: int, is_hls: bool) -> tuple[bool, bool]:
        """Transcode a file using FFmpeg.

        Args:
            fr: FileResolver instance with file information.
            semitones: Semitones to transpose.
            is_hls: Whether to use HLS streaming format.

        Returns:
            Tuple of (is_transcoding_complete, is_buffering_complete).
        """
        self.kill_ffmpeg()

        normalize_audio = self.preferences.get_or_default("normalize_audio")
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )
        avsync = self.preferences.get_or_default("avsync")
        cdg_pixel_scaling = self.preferences.get_or_default("cdg_pixel_scaling")
        buffer_size = int(self.preferences.get_or_default("buffer_size")) * 1000

        ffmpeg_cmd = build_ffmpeg_cmd(
            fr,
            semitones,
            normalize_audio,
            not is_hls,  # force mp4 encoding
            complete_transcode_before_play,
            avsync,
            cdg_pixel_scaling,
            strip_audio=False,
        )
        self.ffmpeg_process = ffmpeg_cmd.run_async(pipe_stderr=True, pipe_stdin=True)

        # FFmpeg outputs to stderr - prevent blocking reads
        self.ffmpeg_log = Queue()
        t = Thread(
            target=enqueue_output,
            args=(self.ffmpeg_process.stderr, self.ffmpeg_log),
            daemon=True,
        )
        t.start()

        # Surface segment-write progress to the UI for as long as ffmpeg runs,
        # so the seek slider can reflect "how much has been prepared".
        if is_hls:
            self._start_ffmpeg_progress_monitor(fr)

        transcode_max_retries = 2500  # ~2 minutes max
        is_transcoding_complete = False
        is_buffering_complete = False

        # Transcoding readiness polling loop
        while True:
            self.log_ffmpeg_output()

            # Check if FFmpeg has exited
            if self.ffmpeg_process.poll() is not None:
                exitcode = self.ffmpeg_process.poll()
                if exitcode != 0:
                    logging.error(f"FFmpeg exited with code {exitcode}")
                    break
                else:
                    is_transcoding_complete = True
                    stream_size = fr.get_current_stream_size()
                    logging.debug(f"Transcoding complete. Output size: {stream_size}")
                    break

            # Check buffering progress based on streaming format
            if is_hls:
                is_buffering_complete = self._check_hls_buffer(fr, buffer_size)
            else:
                is_buffering_complete = self._check_mp4_buffer(fr, buffer_size)

            if is_buffering_complete:
                break

            # Prevent infinite loop
            if transcode_max_retries <= 0:
                logging.error("Max retries reached trying to play song")
                break
            transcode_max_retries -= 1
            time.sleep(0.05)

        return is_transcoding_complete, is_buffering_complete

    def _check_hls_buffer(self, fr: FileResolver, buffer_size: int) -> bool:
        """Check if HLS buffer is ready for playback.

        Counts segment files directly instead of parsing the playlist.
        This works with hls_playlist_type=vod (playlist written at end)
        while still allowing early playback detection.

        Args:
            fr: FileResolver instance.
            buffer_size: Minimum buffer size in bytes.

        Returns:
            True if buffer is ready, False otherwise.
        """
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )
        if complete_transcode_before_play:
            return False

        try:
            # Check if the playlist exists and has content
            if not os.path.exists(fr.output_file):
                return False
            if os.path.getsize(fr.output_file) == 0:
                return False

            # Count segment files directly (works even before playlist is written)
            stream_uid_str = str(fr.stream_uid)
            segment_files = [
                f for f in os.listdir(fr.tmp_dir) if stream_uid_str in f and f.endswith(".m4s")
            ]
            segment_count = len(segment_files)
            # One ~3s segment is enough to start playback; FFmpeg keeps
            # appending segments as the browser consumes them.
            min_segments = 1

            if segment_count >= min_segments:
                stream_size = fr.get_current_stream_size()
                if stream_size >= buffer_size:
                    logging.debug(
                        f"Buffering complete. Stream size: {stream_size}, "
                        f"Segments: {segment_count}"
                    )
                    return True
        except FileNotFoundError:
            pass  # Temp dir doesn't exist yet
        except OSError as e:
            logging.warning(f"I/O error checking buffer: {e}")
        except Exception as e:
            logging.error(f"Unexpected error during buffering check: {e}")

        return False

    def _check_mp4_buffer(self, fr: FileResolver, buffer_size: int) -> bool:
        """Check if MP4 buffer is ready for playback.

        Args:
            fr: FileResolver instance.
            buffer_size: Minimum buffer size in bytes.

        Returns:
            True if buffer is ready, False otherwise.
        """
        complete_transcode_before_play = self.preferences.get_or_default(
            "complete_transcode_before_play"
        )
        if complete_transcode_before_play:
            return False

        try:
            output_file_size = os.path.getsize(fr.output_file)
            if output_file_size > buffer_size:
                logging.debug(f"Buffering complete. File size: {output_file_size}")
                return True
        except FileNotFoundError:
            pass

        return False

    def _prepare_stems(self, fr: FileResolver) -> bool:
        """Register stems for the current song, returning immediately.

        Order of preference:
          1. MP3 cache — register, emit stems_ready, return (no Demucs).
          2. WAV cache — register, emit stems_ready, kick off MP3 encode bg.
          3. Live Demucs — register .partial paths, run ffmpeg extract +
             separation in a background thread. stems_ready fires when the
             first segment is on disk. Frontend starts video with original
             audio and crossfades to stems when the event arrives.

        Returns True if stems are registered (or will be). False only on
        unrecoverable errors before any registration.
        """
        from pikaraoke.lib.demucs_processor import (
            acquire_separation,
            encode_mp3_in_background,
            finalize_partial_stems,
            get_cache_key,
            get_cached_stems,
            partial_stem_paths,
            release_separation,
            resolve_audio_source,
            separate_stems,
        )

        stream_uid = str(fr.stream_uid)
        total_seconds = float(fr.duration or 0)

        # Prefer the sibling audio file (parallel-download pipeline) when
        # present — hashing and decoding audio is cheaper than the muxed
        # mp4, and the key stays stable between prewarm (download time)
        # and play time.
        audio_source = fr.audio_sibling_path or fr.file_path
        resolved_source = resolve_audio_source(audio_source)
        cache_key = get_cache_key(resolved_source)
        cached = get_cached_stems(cache_key)

        if cached:
            self._register_cached_stems(stream_uid, cache_key, cached, total_seconds)
            return True

        # Claim the per-song separation lock. If another thread (download
        # prewarm, lyrics prewarm) is already separating this song, we
        # piggyback on its output instead of starting a parallel run that
        # would race on the same .partial files.
        is_owner, handle = acquire_separation(resolved_source)
        if not is_owner:
            self._attach_to_inflight_separation(stream_uid, cache_key, total_seconds, handle)
            return True

        try:
            return self._run_owned_separation(
                fr, stream_uid, resolved_source, cache_key, total_seconds, handle
            )
        except BaseException:
            # Ownership must always be released so waiters don't hang.
            release_separation(resolved_source, False)
            raise

    def _register_cached_stems(
        self,
        stream_uid: str,
        cache_key: str,
        cached: tuple[str, str, str],
        total_seconds: float,
    ) -> None:
        from pikaraoke.lib.demucs_processor import encode_mp3_in_background

        vocals_path, instrumental_path, fmt = cached
        done = threading.Event()
        done.set()
        ready = threading.Event()
        ready.set()
        self.active_stems[stream_uid] = ActiveStems(
            vocals_path=vocals_path,
            instrumental_path=instrumental_path,
            format=fmt,
            done_event=done,
            ready_event=ready,
            processed_seconds=total_seconds,
            total_seconds=total_seconds,
        )
        self._emit_demucs_progress(total_seconds, total_seconds)
        self._emit_stems_ready(stream_uid)
        # WAV cache -> encode MP3 in background so the next play is smaller.
        if fmt == "wav":
            encode_mp3_in_background(cache_key)

    def _attach_to_inflight_separation(
        self,
        stream_uid: str,
        cache_key: str,
        total_seconds: float,
        handle,
    ) -> None:
        """Register stems that point at another thread's in-flight separation.

        The owner is writing to the same ``.partial`` paths we'd have chosen;
        we share its ``ready_event`` to emit ``stems_ready`` as soon as the
        first segment lands, then swap to the final cache paths once the
        owner signals ``done_event``.
        """
        from pikaraoke.lib.demucs_processor import (
            encode_mp3_in_background,
            get_cached_stems,
            partial_stem_paths,
        )

        partial_v, partial_i = partial_stem_paths(cache_key)
        self.active_stems[stream_uid] = ActiveStems(
            vocals_path=partial_v,
            instrumental_path=partial_i,
            format="wav",
            done_event=handle.done_event,
            ready_event=handle.ready_event,
            processed_seconds=0.0,
            total_seconds=total_seconds,
        )

        def _notify_when_ready() -> None:
            if handle.ready_event.wait(timeout=120):
                logging.info("Demucs: first segment ready (attached)")
                self._emit_stems_ready(stream_uid)

        def _swap_to_final_paths() -> None:
            # Owner signals done (success or failure). On success the partial
            # files were renamed to the final cache names and we need to
            # update our active entry so HTTP tail reads hit the live file.
            handle.done_event.wait()
            if not handle.success:
                logging.warning("Demucs: upstream separation failed; no stems available")
                return
            cached = get_cached_stems(cache_key)
            if cached:
                final_v, final_i, fmt = cached
                entry = self.active_stems.get(stream_uid)
                if entry is not None:
                    entry.vocals_path = final_v
                    entry.instrumental_path = final_i
                    entry.format = fmt
                    entry.processed_seconds = total_seconds
                    entry.total_seconds = total_seconds
            self._emit_demucs_progress(total_seconds, total_seconds)
            # WAV-only cache -> kick off the MP3 encode so future plays are
            # smaller. Idempotent: encode_mp3_in_background is a no-op when
            # the MP3 already exists.
            if cached and cached[2] == "wav":
                encode_mp3_in_background(cache_key)

        threading.Thread(target=_notify_when_ready, daemon=True).start()
        threading.Thread(target=_swap_to_final_paths, daemon=True).start()

    def _run_owned_separation(
        self,
        fr: FileResolver,
        stream_uid: str,
        resolved_source: str,
        cache_key: str,
        total_seconds: float,
        handle,
    ) -> bool:
        """Drive the separation when this call owns the per-song lock."""
        from pikaraoke.lib.demucs_processor import (
            encode_mp3_in_background,
            finalize_partial_stems,
            partial_stem_paths,
            release_separation,
            separate_stems,
        )

        input_wav = os.path.join(fr.tmp_dir, "demucs_input.wav")
        logging.info(f"Demucs: extracting audio from {resolved_source}")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", resolved_source, "-f", "wav", "-ar", "44100", input_wav],
            capture_output=True,
        )
        if result.returncode != 0:
            logging.error(f"FFmpeg audio extraction failed: {result.stderr.decode()}")
            release_separation(resolved_source, False)
            return False

        # Tier 3: live Demucs. Register .partial paths immediately; the HTTP
        # tail route has a grace period that waits for the file to appear.
        partial_v, partial_i = partial_stem_paths(cache_key)
        done_event = threading.Event()

        self.active_stems[stream_uid] = ActiveStems(
            vocals_path=partial_v,
            instrumental_path=partial_i,
            format="wav",
            done_event=done_event,
            ready_event=handle.ready_event,
            processed_seconds=0.0,
            total_seconds=total_seconds,
        )

        # Lock synchronizes the bg-thread rename (.partial -> .wav via
        # finalize_partial_stems) with any path reads on the entry.
        finalize_lock = threading.Lock()
        last_emit = [0.0]  # [timestamp] — throttle broadcasts to ~1/s

        def progress_cb(processed: float, total: float) -> None:
            entry = self.active_stems.get(stream_uid)
            if entry is not None:
                entry.processed_seconds = processed
                entry.total_seconds = total
            now = time.monotonic()
            if processed >= total or (now - last_emit[0]) >= 1.0:
                last_emit[0] = now
                self._emit_demucs_progress(processed, total)

        def _separate_and_finalize() -> None:
            ok = False
            try:
                ok = separate_stems(
                    input_wav, partial_v, partial_i, handle.ready_event, progress_cb
                )
                if ok:
                    with finalize_lock:
                        final_v, final_i = finalize_partial_stems(cache_key)
                        entry = self.active_stems.get(stream_uid)
                        if entry is not None:
                            entry.vocals_path = final_v
                            entry.instrumental_path = final_i
                    encode_mp3_in_background(cache_key)
            finally:
                done_event.set()
                release_separation(resolved_source, ok)

        def _notify_when_ready() -> None:
            if handle.ready_event.wait(timeout=120):
                logging.info("Demucs: first segment ready")
                self._emit_stems_ready(stream_uid)

        threading.Thread(target=_separate_and_finalize, daemon=True).start()
        threading.Thread(target=_notify_when_ready, daemon=True).start()

        return True

    def _emit_stems_ready(self, stream_uid: str) -> None:
        if self.events is None:
            return
        entry = self.active_stems.get(stream_uid)
        if entry is None:
            return
        ext = entry.format  # "wav" or "mp3"
        try:
            self.events.emit(
                "stems_ready",
                {
                    "stream_uid": stream_uid,
                    "vocals_url": f"/stream/{stream_uid}/vocals.{ext}",
                    "instrumental_url": f"/stream/{stream_uid}/instrumental.{ext}",
                },
            )
        except Exception:
            logging.exception("Failed to emit stems_ready event")

    def _emit_demucs_progress(self, processed: float, total: float) -> None:
        if self.events is None:
            return
        try:
            self.events.emit(
                "demucs_progress", {"processed": float(processed), "total": float(total)}
            )
        except Exception:
            logging.exception("Failed to emit demucs_progress event")

    def _start_ffmpeg_progress_monitor(self, fr: FileResolver) -> None:
        """Watch HLS segments on disk until ffmpeg exits; emit ffmpeg_progress.

        Segment count x HLS_SEGMENT_DURATION is a lower bound on how many
        seconds of video are ready to seek into — clients use it to clamp
        the seek slider to the prepared range.
        """
        proc = self.ffmpeg_process
        if proc is None or self.events is None:
            return
        total_seconds = float(fr.duration or 0)
        if total_seconds <= 0:
            return
        tmp_dir = fr.tmp_dir
        stream_uid_str = str(fr.stream_uid)

        def _poll() -> None:
            last_emitted = -1.0
            while True:
                rc = proc.poll()
                exited = rc is not None
                try:
                    count = sum(
                        1 for f in os.listdir(tmp_dir) if stream_uid_str in f and f.endswith(".m4s")
                    )
                except (FileNotFoundError, OSError):
                    count = 0
                processed = min(count * HLS_SEGMENT_DURATION, total_seconds)
                # Clean exit means the whole file is transcoded; unlock the
                # rest of the slider even if segment accounting under-counted.
                if exited and rc == 0:
                    processed = total_seconds
                if processed != last_emitted:
                    last_emitted = processed
                    self._emit_ffmpeg_progress(processed, total_seconds)
                if exited:
                    return
                time.sleep(1.0)

        threading.Thread(target=_poll, daemon=True).start()

    def _emit_ffmpeg_progress(self, processed: float, total: float) -> None:
        if self.events is None:
            return
        try:
            self.events.emit(
                "ffmpeg_progress", {"processed": float(processed), "total": float(total)}
            )
        except Exception:
            logging.exception("Failed to emit ffmpeg_progress event")

    def log_ffmpeg_output(self) -> None:
        """Log any pending FFmpeg output from the queue."""
        if self.ffmpeg_log is None:
            return
        while self.ffmpeg_log.qsize() > 0:
            output = self.ffmpeg_log.get_nowait()
            logging.debug("[FFMPEG] " + output.decode("utf-8", "ignore").strip())
        if self.ffmpeg_process is not None:
            rc = self.ffmpeg_process.poll()
            if rc is not None and getattr(self, "_ffmpeg_exit_logged", None) != id(
                self.ffmpeg_process
            ):
                if rc != 0:
                    logging.error(f"[FFMPEG] process exited with code {rc}")
                else:
                    logging.debug(f"[FFMPEG] process exited cleanly (code 0)")
                self._ffmpeg_exit_logged = id(self.ffmpeg_process)

    def clear_active_stems(self) -> None:
        """Drop all registered stem entries.

        In-flight stream generators already hold their own references to the
        ActiveStems object, so clearing here doesn't interrupt them — it just
        prevents the dict from growing unboundedly across songs and ensures
        late requests for a finished song get 404 instead of stale data.
        """
        self.active_stems.clear()

    def clear_active_sources(self) -> None:
        """Drop all registered direct-mp4 source paths."""
        self.active_sources.clear()

    def clear_active_audio(self) -> None:
        """Drop all registered audio pipe configs."""
        self.active_audio.clear()

    def kill_ffmpeg(self) -> None:
        """Terminate the running FFmpeg process gracefully.

        Uses SIGTERM first, then SIGKILL if needed.
        Critical for Raspberry Pi to release GPU memory from h264_v4l2m2m encoder.
        """
        if self.ffmpeg_process:
            logging.debug("Terminating ffmpeg process gracefully")
            try:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=5)
                logging.debug("FFmpeg process terminated gracefully")
            except subprocess.TimeoutExpired:
                logging.warning("FFmpeg did not terminate gracefully, forcing kill")
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait()
                logging.debug("FFmpeg process force killed")
            except Exception as e:
                logging.debug(f"FFmpeg termination exception: {e}")
            finally:
                self.ffmpeg_process = None
