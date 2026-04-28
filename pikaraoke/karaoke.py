"""Core karaoke engine for managing songs, queue, and playback."""

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import qrcode
from flask_babel import _
from qrcode.image.pure import PyPNGImage

from pikaraoke.lib.download_manager import DownloadManager
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import (
    get_ffmpeg_version,
    is_transpose_enabled,
    supports_hardware_h264_encoding,
)
from pikaraoke.lib.get_platform import (
    get_data_directory,
    get_os_version,
    get_platform,
    is_raspberry_pi,
)
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.library_scanner import LibraryScanner, ScanResult
from pikaraoke.lib.lyrics import LyricsService
from pikaraoke.lib.network import get_ip
from pikaraoke.lib.playback_controller import PlaybackController
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_manager import SongManager
from pikaraoke.lib.state_persistence import StatePersistence
from pikaraoke.lib.youtube_dl import (
    get_search_results,
    get_youtubedl_version,
    upgrade_youtubedl,
)
from pikaraoke.version import __version__ as VERSION

_WHISPERX_OPT_OUT = {"off", "none", "false", "0"}

# The aligner no longer runs whisper ASR - it feeds LRC text directly to
# wav2vec2 CTC forced alignment. There's no transcription-model size knob
# anymore; WHISPERX_MODEL now only acts as an on/off switch ("off"/"none"/
# "false"/"0" disable, anything else enables).
_ALIGNER_ID = "wav2vec2"


def word_level_lyrics_status() -> dict:
    """Whether word-level karaoke alignment is active, and why / why not.

    Auto-enables when whisperx is installed and the user hasn't explicitly
    opted out. Returned dict:

        enabled (bool): True when whisperx is importable and not opted-out.
        model (str | None): Aligner identifier ("wav2vec2") when enabled.
        device (str | None): The resolved torch device when enabled.
        reason (str | None): Human-readable reason why alignment is off.
        fix (str | None): One-line suggestion for the user to fix it.
        explicit_opt_out (bool): True when the user set WHISPERX_MODEL=off.

    Shared by the startup banner and the Info page so they report consistently.
    """
    env_raw = os.environ.get("WHISPERX_MODEL", "").strip().lower()
    if env_raw in _WHISPERX_OPT_OUT:
        return {
            "enabled": False,
            "model": None,
            "device": None,
            "reason": "opted out via WHISPERX_MODEL=off",
            "fix": None,
            "explicit_opt_out": True,
        }
    if not _is_whisperx_installed():
        return {
            "enabled": False,
            "model": None,
            "device": None,
            "reason": "whisperx is not installed",
            "fix": "pip install 'pikaraoke[align]'",
            "explicit_opt_out": False,
        }
    device = os.environ.get("WHISPERX_DEVICE", "").strip() or _auto_whisperx_device()
    return {
        "enabled": True,
        "model": _ALIGNER_ID,
        "device": device,
        "reason": None,
        "fix": None,
        "explicit_opt_out": False,
    }


def _is_whisperx_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("whisperx") is not None


def _auto_whisperx_device() -> str:
    """Prefer CUDA when torch sees it; fall back to CPU.

    whisperx's faster-whisper backend (CTranslate2) has no MPS support today,
    so Apple Silicon users stay on CPU here even when torch reports MPS.
    """
    try:
        import torch
    except ImportError:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except (RuntimeError, AttributeError):
        pass
    return "cpu"


def _build_lyrics_aligner():
    """Return a WhisperXAligner or None, emitting a startup banner when disabled."""
    status = word_level_lyrics_status()
    if not status["enabled"]:
        if not status["explicit_opt_out"]:
            _warn_word_level_disabled(reason=status["reason"], fix=status["fix"])
        return None

    from pikaraoke.lib.lyrics_align import WhisperXAligner

    logging.info(
        "word-level alignment enabled (aligner=%s, device=%s)",
        status["model"],
        status["device"],
    )
    return WhisperXAligner(device=status["device"])


def _prewarm_vad() -> None:
    """Warm the silero VAD JIT model in a daemon thread.

    The model is ~2 MB and loads in 200-500 ms on a Pi 4. Running the
    load on startup keeps that latency off the first song's
    LRC->audio anchor probe. Errors are logged inside ``vad_probe`` -
    silero failure transparently routes to the silencedetect fallback.
    """

    def _run() -> None:
        try:
            from pikaraoke.lib import vad_probe

            vad_probe._ensure_model()
        except Exception:
            logging.exception("vad_probe: prewarm thread crashed")

    threading.Thread(target=_run, name="vad-prewarm", daemon=True).start()


def _warn_word_level_disabled(reason: str, fix: str) -> None:
    """Print a high-visibility startup banner when word-level captions are off."""
    width = 78
    inner = width - 2

    def line(text: str) -> str:
        return "*" + text.ljust(inner) + "*"

    lines = [
        "",
        "*" * width,
        "*" + " WARNING: word-level karaoke captions are DISABLED ".center(inner) + "*",
        line(""),
        line(f"  Reason:  {reason}"),
        line(f"  Fix:     {fix}"),
        line("  Silence: export WHISPERX_MODEL=off"),
        line(""),
        line("  Lyrics will still render line-by-line from LRCLib."),
        line("  Only the syllable-level karaoke highlight is skipped."),
        "*" * width,
        "",
    ]
    banner = "\n".join(lines)
    if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
        banner = f"\033[1;31m{banner}\033[0m"
    logging.warning(banner)


class Karaoke:
    """Main karaoke engine managing songs, queue, and playback.

    This class handles all core karaoke functionality including:
    - Song queue management
    - YouTube video downloading
    - Playback coordination via PlaybackController
    - User preferences
    - QR code generation

    Attributes:
        available_songs: List of available song file paths.
        queue_manager: Queue management for songs.
        playback_controller: Playback state and stream coordination.
        volume: Current volume level (0.0 to 1.0).
    """

    song_manager: SongManager
    queue_manager: QueueManager
    playback_controller: PlaybackController

    now_playing_notification: str | None = None
    volume: float

    qr_code_path: str | None = None
    base_path: str = os.path.dirname(__file__)
    loop_interval: int = 500  # in milliseconds
    default_logo_path: str = os.path.join(base_path, "static", "images", "logo.png")
    default_bg_music_path: str = os.path.join(base_path, "static", "music")
    default_bg_video_path: str = os.path.join(base_path, "static", "video", "night_sea.mp4")
    screensaver_timeout: int

    normalize_audio: bool
    show_splash_clock: bool

    # Download manager for serialized downloads
    download_manager: DownloadManager

    # Event system and preferences
    events: EventSystem
    preferences: PreferenceManager

    def __init__(
        self,
        # Non-preference parameters (keep their own defaults)
        additional_ytdl_args: str | None = None,
        bg_music_path: str | None = None,
        bg_video_path: str | None = None,
        config_file_path: str = "config.ini",
        download_path: str = "/usr/lib/pikaraoke/songs",
        log_level: int = logging.DEBUG,
        logo_path: str | None = None,
        port: int = 5555,
        prefer_hostname: bool | None = None,
        preferred_language: str | None = None,
        socketio=None,
        streaming_format: str = "hls",
        url: str | None = None,
        youtubedl_proxy: str | None = None,
        # Preference parameters (defaults from PreferenceManager.DEFAULTS)
        avsync: float | None = None,
        bg_music_volume: float | None = None,
        browse_results_per_page: int | None = None,
        buffer_size: int | None = None,
        cdg_pixel_scaling: bool | None = None,
        complete_transcode_before_play: bool | None = None,
        disable_bg_music: bool | None = None,
        disable_bg_video: bool | None = None,
        disable_score: bool | None = None,
        hide_notifications: bool | None = None,
        hide_overlay: bool | None = None,
        hide_url: bool | None = None,
        high_quality: bool | None = None,
        limit_user_songs_by: int | None = None,
        normalize_audio: bool | None = None,
        screensaver_timeout: int | None = None,
        show_splash_clock: bool | None = None,
        splash_delay: int | None = None,
        volume: float | None = None,
        enable_title_tidy: bool | None = None,
    ) -> None:
        """Initialize the Karaoke instance.

        Args:
            port: HTTP server port number.
            download_path: Directory path for downloaded songs.
            hide_url: Hide URL and QR code on splash screen.
            hide_notifications: Disable notification popups.
            high_quality: Download higher quality videos (up to 1080p).
            volume: Default volume level (0.0 to 1.0).
            normalize_audio: Apply loudness normalization.
            complete_transcode_before_play: Buffer entire file before playback.
            buffer_size: Transcode buffer size in KB.
            log_level: Logging level (e.g., logging.DEBUG).
            splash_delay: Seconds to wait between songs.
            youtubedl_proxy: Proxy URL for yt-dlp.
            logo_path: Custom logo image path.
            hide_overlay: Hide video overlay.
            screensaver_timeout: Screensaver activation delay in seconds.
            url: Override auto-detected URL.
            prefer_hostname: Use hostname instead of IP in URL.
            disable_bg_music: Disable background music.
            bg_music_volume: Background music volume (0.0 to 1.0).
            bg_music_path: Directory for background music files.
            bg_video_path: Path to background video file.
            disable_bg_video: Disable background video.
            disable_score: Disable score screen.
            limit_user_songs_by: Max songs per user in queue (0 = unlimited).
            avsync: Audio/video sync adjustment in seconds.
            config_file_path: Path to config.ini file.
            cdg_pixel_scaling: Enable CDG pixel scaling.
            streaming_format: Video streaming format ('hls' or 'mp4').
            browse_results_per_page: Number of search results per page.
            additional_ytdl_args: Additional yt-dlp command arguments.
            socketio: SocketIO instance for real-time event emission.
            preferred_language: Language code for UI (e.g., 'en', 'de_DE').
        """
        logging.basicConfig(
            format="[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=int(log_level),
        )

        # Initialize event system and preferences (foundation for all components)
        self.events = EventSystem()
        self.preferences = PreferenceManager(config_file_path, target=self)

        # Platform-specific initializations
        self.platform = get_platform()
        self.os_version = get_os_version()
        self.ffmpeg_version = get_ffmpeg_version()
        self.is_transpose_enabled = is_transpose_enabled()
        self.supports_hardware_h264_encoding = supports_hardware_h264_encoding()
        self.youtubedl_version = get_youtubedl_version()
        self.is_raspberry_pi = is_raspberry_pi()

        logging.info("PiKaraoke version: " + VERSION)

        # Set non-preference attributes (not stored in config)
        self.port = port
        self.download_path = download_path
        self.log_level = log_level
        self.youtubedl_proxy = youtubedl_proxy
        self.additional_ytdl_args = additional_ytdl_args
        self.logo_path = self.default_logo_path if logo_path is None else logo_path
        self.prefer_hostname = prefer_hostname
        self.bg_music_path = self.default_bg_music_path if bg_music_path is None else bg_music_path
        self.bg_video_path = self.default_bg_video_path if bg_video_path is None else bg_video_path
        self.streaming_format = streaming_format
        self.socketio = socketio
        self.url_override = url
        self.url = self.get_url()

        # Load all preference-driven attributes from config (with CLI overrides as fallback)
        cli_args = {k: v for k, v in locals().items() if k != "self"}
        self._load_preferences(**cli_args)

        # Log the settings to debug level
        self.log_settings_to_debug()

        # Initialize database, scanner, and song manager (startup runs at end of __init__)
        self.db = KaraokeDatabase()
        self.song_manager = SongManager(
            self.download_path, db=self.db, get_title_tidy=lambda: self.enable_title_tidy
        )
        self._scanner = LibraryScanner(
            self.db,
            on_provenance_classified=self._invalidate_stale_alignments_from_db,
        )
        self._sync_lock = threading.Lock()

        # Rolling log of song_warning events. Persisted to the metadata kv so
        # operators can open the admin view after a restart and still see what
        # went wrong. Capped to avoid unbounded growth.
        self._song_warnings: list[dict[str, Any]] = self._load_song_warnings()
        self._song_warnings_lock = threading.Lock()

        self.generate_qr_code()

        # Clean up half-written Demucs stems from any previous run.
        try:
            from pikaraoke.lib.demucs_processor import cleanup_stale_partials

            cleanup_stale_partials()
        except Exception:
            logging.exception("Failed to clean up stale Demucs partials")

        # Initialize playback controller for video playback and FFmpeg coordination
        self.playback_controller = PlaybackController(
            preferences=self.preferences,
            events=self.events,
            filename_from_path=self.song_manager.display_name_from_path,
            streaming_format=self.streaming_format,
            db=self.db,
        )

        # Lyrics auto-fetch from LRCLib; optional per-word forced alignment via whisperx.
        self._aligner_instance = _build_lyrics_aligner()
        self.lyrics_service = LyricsService(
            download_path=self.download_path,
            events=self.events,
            aligner=self._aligner_instance,
            db=self.db,
        )

        # Prewarm silero VAD once per process so the first song's per-line
        # LRC->audio anchor probe doesn't pay the model-load latency. The
        # thread is daemon: a server shutdown during prewarm exits cleanly.
        # Falls back to ffmpeg silencedetect when silero isn't installed -
        # logged inside vad_probe so operators see it.
        _prewarm_vad()

        # Event bridging: the coordinator wires manager events to the UI (SocketIO/notifications).
        self.events.on("notification", self.log_and_send)
        self.events.on(
            "queue_update",
            lambda: self.socketio.emit("queue_update", namespace="/") if self.socketio else None,
        )
        self.events.on("now_playing_update", self.update_now_playing_socket)
        self.events.on("playback_started", self.update_now_playing_socket)
        self.events.on("song_ended", self.update_now_playing_socket)
        self.events.on("skip_requested", lambda: self.playback_controller.skip(False))
        self.events.on("song_downloaded", self.song_manager.register_download)
        # US-15/US-31 P1: fingerprint audio at download time too, so a
        # re-download with bit-flipped content invalidates stems + auto .ass
        # before the next play instead of waiting for the play-time check.
        self.events.on("song_downloaded", self._ensure_fingerprint_on_download)
        # Run lyrics_service.fetch_and_convert off the synchronous listener
        # chain: LRCLib + iTunes calls can block up to ~10s per song (US-10).
        # The event system invokes listeners serially on the download worker
        # thread, so a slow fetch stalls the queue for the next song.
        self.events.on("song_downloaded", self._dispatch_lyrics_fetch_async)
        self.events.on("lyrics_upgraded", self._on_lyrics_upgraded)
        self.events.on(
            "sync_started",
            lambda: self.socketio.emit("sync_started", namespace="/") if self.socketio else None,
        )
        self.events.on(
            "sync_finished",
            lambda: self.socketio.emit("sync_finished", namespace="/") if self.socketio else None,
        )
        self.events.on(
            "download_progress",
            lambda data: (
                self.socketio.emit("download_progress", data, namespace="/")
                if self.socketio
                else None
            ),
        )
        self.events.on(
            "demucs_progress",
            lambda data: (
                self.socketio.emit("demucs_progress", data, namespace="/")
                if self.socketio
                else None
            ),
        )
        self.events.on(
            "ffmpeg_progress",
            lambda data: (
                self.socketio.emit("ffmpeg_progress", data, namespace="/")
                if self.socketio
                else None
            ),
        )
        self.events.on(
            "stems_ready",
            lambda data: (
                self.socketio.emit("stems_ready", data, namespace="/") if self.socketio else None
            ),
        )
        self.events.on("song_warning", self._handle_song_warning)

        # Wire the demucs_processor module-level warning hook so that
        # background prewarm paths (download_manager, lyrics) — which
        # don't hold a StreamManager reference — can surface failures as
        # song_warning events too.
        from pikaraoke.lib import demucs_processor

        def _forward_warning(song: str | None, message: str, detail: str) -> None:
            self.events.emit(
                "song_warning",
                {
                    "message": message,
                    "detail": detail,
                    "song": song or "",
                    "severity": "warning",
                },
            )

        demucs_processor.set_warning_hook(_forward_warning)

        # Prewarm hooks (US-7): surface Demucs progress and completion for
        # songs that are being separated before playback starts. The
        # payloads carry ``song_basename`` so the client can decide whether
        # the event applies to the currently-playing song (live prewarm is
        # indistinguishable from in-flight play-time separation on-wire).
        def _forward_progress(song: str | None, processed: float, total: float) -> None:
            self.events.emit(
                "demucs_progress",
                {
                    "processed": float(processed),
                    "total": float(total),
                    "song_basename": song or "",
                },
            )

        def _forward_ready(song: str | None, cache_key: str) -> None:
            self.events.emit(
                "stems_ready",
                {"song_basename": song or "", "cache_key": cache_key},
            )

        demucs_processor.set_progress_hook(_forward_progress)
        demucs_processor.set_ready_hook(_forward_ready)

        # Active-stream probe (US-32): the prewarm-mode MP3 encoder asks
        # this before unlinking WAVs so a live byte-range request never
        # has the file deleted mid-stream. Returns True when any
        # registered stream is reading stems for the given cache_key.
        demucs_processor.set_active_check_hook(
            self.playback_controller.stream_manager.is_cache_key_active
        )

        # Initialize queue manager
        self.queue_manager = QueueManager(
            preferences=self.preferences,
            events=self.events,
            get_now_playing_user=lambda: self.playback_controller.now_playing_user,
            filename_from_path=self.song_manager.display_name_from_path,
            get_available_songs=lambda: self.song_manager.songs,
        )

        # Initialize and start download manager
        self.download_manager = DownloadManager(
            events=self.events,
            preferences=self.preferences,
            song_manager=self.song_manager,
            queue_manager=self.queue_manager,
            download_path=self.download_path,
            youtubedl_proxy=self.youtubedl_proxy,
            additional_ytdl_args=self.additional_ytdl_args,
            db=self.db,
        )
        self.download_manager.start()

        # Song library startup: warm cache from DB or blocking cold scan.
        # The scanner's _backfill_artifacts pass classifies each .ass into
        # auto_word/auto_line/user (lyrics_provenance), then fires our
        # injected _invalidate_stale_alignments_from_db sweep so any
        # word-level files produced by an older aligner are dropped before
        # _verify_integrity / _maybe_initial_reprocess_with_whisperx runs.
        paths = self.db.get_all_song_paths()
        if paths:
            self.song_manager.songs.update(paths)
            logging.info("Loaded songs from database, syncing in the background")
            self.sync_library()
        else:
            logging.info("No existing database found, scanning song directory")
            result = self._scanner.scan(self.download_path)
            self._apply_scan_result(result)

        # If whisperx is newly available, upgrade line-level .ass files in the
        # background. Gated on a one-shot DB sentinel so we don't re-scan the
        # whole library on every restart — the first successful pass sets the
        # flag and subsequent startups skip straight past (US-17).
        self._maybe_initial_reprocess_with_whisperx()

        # Restore queue / now-playing / master volume from the previous run.
        self.state_persistence = StatePersistence()
        self._last_persist = 0.0
        self._restore_state()

    def _invalidate_stale_alignments_from_db(self) -> None:
        """Invalidate cached word-level .ass whose aligner_model is stale.

        Queries the DB for songs with ``lyrics_provenance = 'auto_word'``
        whose ``aligner_model`` differs from (or is NULL relative to) the
        current aligner's ``model_id``, then routes each through
        ``invalidate_auto_ass`` - the canonical primitive that unlinks
        the file, drops the artifact row, and clears the DB columns so
        the next playback re-runs alignment with the new model.

        Wired into ``LibraryScanner`` as ``on_provenance_classified`` so
        it fires after ``_backfill_artifacts`` has stamped provenance on
        any pre-tracking files. No-op when the aligner isn't configured.
        """
        if self._aligner_instance is None:
            return
        current = getattr(self._aligner_instance, "model_id", None)
        if not isinstance(current, str) or not current:
            return
        from pikaraoke.lib.audio_fingerprint import invalidate_auto_ass

        ids = self.db.get_song_ids_for_realignment(current)
        for song_id in ids:
            try:
                invalidate_auto_ass(self.db, song_id)
            except Exception:
                logging.exception(
                    "stale-alignment sweep: invalidate_auto_ass failed for song_id=%s",
                    song_id,
                )
        if ids:
            logging.info(
                "stale-alignment sweep: invalidated %d cached .ass (current aligner=%s)",
                len(ids),
                current,
            )

    _WHISPERX_REPROCESS_SENTINEL = "whisperx_initial_reprocess_done"

    def _maybe_initial_reprocess_with_whisperx(self) -> None:
        """Run `reprocess_library` exactly once after whisperx becomes available.

        Without the sentinel, every restart scans the whole library to look
        for line-level `.ass` files that could be upgraded to word-level —
        wasted work after the first pass (US-17).
        """
        if not self.lyrics_service.has_aligner:
            return
        if self.db.get_metadata(self._WHISPERX_REPROCESS_SENTINEL) == "1":
            return
        self.lyrics_service.reprocess_library(list(self.song_manager.songs))
        # Record the sentinel even when the batch was empty. Every startup
        # with an aligner available counts as "we've offered the upgrade" —
        # freshly-downloaded songs take the word-level path during their own
        # fetch_and_convert run.
        self.db.set_metadata(self._WHISPERX_REPROCESS_SENTINEL, "1")

    def _apply_scan_result(self, result: ScanResult) -> None:
        """Update SongList and emit notifications after a scan."""
        if result.added or result.moved or result.deleted:
            self.song_manager.songs.update(self.db.get_all_song_paths())
            parts = [
                label
                for count, label in [
                    (result.added, f"{result.added} added"),
                    (result.moved, f"{result.moved} moved"),
                    (result.deleted, f"{result.deleted} removed"),
                ]
                if count
            ]
            self.events.emit("notification", f"Library updated: {', '.join(parts)}", "success")

        if result.circuit_tripped:
            logging.error(
                f"Circuit breaker tripped: >50% of songs missing. "
                f"Drive may be unmounted: {self.download_path}"
            )
            self.events.emit(
                "notification",
                f"Song scan halted: too many songs missing. "
                f"Check your song directory: {self.download_path}. "
                "Click 'Sync Now' to retry after fixing.",
                "danger",
            )
            return

        if result.reprocess_paths:
            self.events.emit(
                "notification",
                f"Library integrity: {len(result.reprocess_paths)} song(s) "
                "queued for lyrics reprocess",
                "success",
            )
            self._dispatch_reprocess(result.reprocess_paths)

        logging.info(f"Scan complete: {result}")

    def _dispatch_reprocess(self, song_paths: list[str]) -> None:
        """Run the lyrics pipeline for a batch of songs serially in one daemon thread.

        Serial dispatch matters at startup: with a thousand songs flagged
        we'd otherwise launch a thousand fetch_and_convert threads, each
        racing for the aligner / Demucs / network. The lyrics pipeline is
        idempotent (cache hits short-circuit) so duplicate dispatches across
        back-to-back scans are harmless.
        """

        def _run() -> None:
            for path in song_paths:
                try:
                    self.lyrics_service.fetch_and_convert(path)
                except Exception:
                    logging.exception("integrity reprocess failed for %s", path)

        threading.Thread(
            target=_run,
            name="library-integrity-reprocess",
            daemon=True,
        ).start()

    def sync_library(self) -> bool:
        """Trigger a background library scan.

        Used for both warm startup reconciliation and admin 'Sync Now'.
        Returns False if a sync is already in progress.
        """
        if not self._sync_lock.acquire(blocking=False):
            return False
        self.events.emit("sync_started")
        thread = threading.Thread(target=self._background_sync, daemon=True)
        thread.start()
        return True

    def _background_sync(self) -> None:
        try:
            logging.info(f"Background library scan starting: {self.download_path}")
            result = self._scanner.scan(self.download_path)
            self._apply_scan_result(result)
        finally:
            self._sync_lock.release()
            self.events.emit("sync_finished")

    def _load_preferences(self, **cli_overrides: Any) -> None:
        """Load preference-driven attributes from config file.

        Priority: CLI argument (if provided) > config file > PreferenceManager.DEFAULTS
        """
        self.preferences.apply_all(**cli_overrides)

    def get_url(self):
        """Get the URL for accessing the PiKaraoke web interface.

        On Raspberry Pi, retries getting the IP address for up to 30 seconds
        in case the network is still initializing at startup.

        Returns:
            URL string in format http://ip:port
        """
        if self.is_raspberry_pi:
            # retry in case pi is still starting up
            # and doesn't have an IP yet (occurs when launched from /etc/rc.local)
            end_time = int(time.time()) + 30
            while int(time.time()) < end_time:
                addresses_str = (
                    subprocess.check_output(["hostname", "-I"]).strip().decode("utf-8", "ignore")
                )
                addresses = addresses_str.split(" ")
                self.ip = addresses[0]
                if len(self.ip) < 7:
                    logging.debug("Couldn't get IP, retrying....")
                else:
                    break
        else:
            self.ip = get_ip(self.platform)

        logging.debug("IP address (for QR code and splash screen): " + self.ip)

        if self.url_override != None:
            logging.debug("Overriding URL with " + self.url_override)
            url = self.url_override
        else:
            if self.prefer_hostname:
                url = f"http://{socket.getfqdn().lower()}:{self.port}"
            else:
                url = f"http://{self.ip}:{self.port}"
        return url

    def log_settings_to_debug(self) -> None:
        """Log all current settings at debug level."""
        output = ""
        for key, value in sorted(vars(self).items()):
            output += f"  {key}: {value}\n"
        logging.debug("\n\n" + output)

    def generate_qr_code(self) -> None:
        """Generate a QR code image for the web interface URL."""
        logging.debug("Generating URL QR code")
        qr = qrcode.QRCode(
            version=1,
            box_size=1,
            border=4,
        )
        qr.add_data(self.url)
        qr.make()
        img = qr.make_image(image_factory=PyPNGImage)
        # Use writable data directory instead of program directory
        data_dir = get_data_directory()
        self.qr_code_path = os.path.join(data_dir, "qrcode.png")
        img.save(self.qr_code_path)  # type: ignore[arg-type]

    def send_notification(self, message: str, color: str = "primary") -> None:
        """Send a notification to the web interface.

        Args:
            message: Notification message text.
            color: Bulma color class (primary, warning, success, danger).
        """
        # Color should be bulma compatible: primary, warning, success, danger
        hide_notifications = self.preferences.get_or_default("hide_notifications")
        if not hide_notifications:
            # don't allow new messages to clobber existing commands, one message at a time
            # other commands have a higher priority
            if self.now_playing_notification != None:
                return
            self.now_playing_notification = message + "::is-" + color
            # Emit notification via SocketIO for event-driven architecture
            if self.socketio:
                self.socketio.emit("notification", self.now_playing_notification, namespace="/")

    def log_and_send(self, message: str, category: str = "info") -> None:
        """Log a message and send it as a notification.

        Args:
            message: Message to log and display.
            category: Message category (info, success, warning, danger).
        """
        # Category should be one of: info, success, warning, danger
        if category == "success":
            logging.info(message)
            self.send_notification(message, "success")
        elif category == "warning":
            logging.warning(message)
            self.send_notification(message, "warning")
        elif category == "danger":
            logging.error(message)
            self.send_notification(message, "danger")
        else:
            logging.info(message)
            self.send_notification(message, "primary")

    def transpose_current(self, semitones: int) -> None:
        """Restart the current song with a new transpose value.

        Args:
            semitones: Number of semitones to transpose.
        """
        filename = self.playback_controller.now_playing_filename
        user = self.playback_controller.now_playing_user
        now_playing = self.playback_controller.now_playing

        if filename is None or user is None:
            logging.warning("Cannot transpose: no song currently playing")
            return
        # MSG: Message shown after the song is transposed, first is the semitones and then the song name
        self.log_and_send(_("Transposing by %s semitones: %s") % (semitones, now_playing))
        # Insert the same song at the top of the queue with transposition
        self.queue_manager.enqueue(filename, user, semitones, True)
        self.playback_controller.skip(log_action=False)

    def volume_change(self, vol_level: float) -> bool:
        """Set the playback volume.

        Mirrors the value into both stem volumes: the single slider is the
        only control the user sees before Demucs produces stems, so this
        keeps the level they chose when the dual-slider UI flips in (US-22),
        and lets the splash apply it to the vocal/instrumental gain nodes
        as soon as stems are audible.

        Args:
            vol_level: Volume level (0.0 to 1.0).

        Returns:
            True after setting volume.
        """
        vol_level = max(0.0, min(1.0, float(vol_level)))
        self.volume = vol_level
        self.vocal_volume = vol_level
        self.instrumental_volume = vol_level
        self.preferences.set("vocal_volume", vol_level)
        self.preferences.set("instrumental_volume", vol_level)
        if self.socketio:
            self.socketio.emit(
                "stem_volume",
                {"vocal_volume": vol_level, "instrumental_volume": vol_level},
                namespace="/",
            )
        # MSG: Message shown after the volume is changed, will be followed by the volume level
        self.log_and_send(_("Volume: %s") % (int(self.volume * 100)))
        self.update_now_playing_socket()
        return True

    def vocal_volume_change(self, vol_level: float) -> bool:
        """Set the vocal stem volume. Applied client-side via Web Audio."""
        vol_level = max(0.0, min(1.0, float(vol_level)))
        self.preferences.set("vocal_volume", vol_level)
        self.vocal_volume = vol_level
        self.log_and_send(_("Vocal volume: %s") % (int(vol_level * 100)))
        self.update_now_playing_socket()
        return True

    def instrumental_volume_change(self, vol_level: float) -> bool:
        """Set the instrumental stem volume. Applied client-side via Web Audio."""
        vol_level = max(0.0, min(1.0, float(vol_level)))
        self.preferences.set("instrumental_volume", vol_level)
        self.instrumental_volume = vol_level
        self.log_and_send(_("Instrumental volume: %s") % (int(vol_level * 100)))
        self.update_now_playing_socket()
        return True

    def vol_up(self) -> None:
        """Increase volume by 10%."""
        new_vol = min(self.volume + 0.1, 1.0)
        self.volume_change(new_vol)
        logging.debug(f"Increasing volume by 10%: {self.volume}")

    def vol_down(self) -> None:
        """Decrease volume by 10%."""
        new_vol = max(self.volume - 0.1, 0.0)
        self.volume_change(new_vol)
        logging.debug(f"Decreasing volume by 10%: {self.volume}")

    def restart(self) -> bool:
        """Restart the current song from the beginning.

        Returns:
            True if successful, False if nothing playing.
        """
        if self.playback_controller.is_playing:
            now_playing = self.playback_controller.now_playing
            logging.info("Restarting: " + (now_playing or "unknown song"))
            # Reset server-side position to 0 and broadcast a seek so every
            # client (not just the one that clicked Restart) rewinds — without
            # this, the effect depends entirely on the client's JS handler.
            self.playback_controller.now_playing_position = 0.0
            self.playback_controller.position_updated_at = time.time()
            self.playback_controller.is_paused = False
            if self.socketio:
                self.socketio.emit("seek", 0.0, namespace="/")
            self.update_now_playing_socket()
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self) -> None:
        """Stop the karaoke run loop."""
        self.running = False

    def handle_run_loop(self) -> None:
        """Handle one iteration of the main run loop with a sleep interval."""
        time.sleep(self.loop_interval / 1000)

    def _restore_state(self) -> None:
        """Rehydrate queue, now-playing, and master volume from disk.

        The saved now-playing song (if any) is prepended to the queue and the
        computed resume position is stashed on the PlaybackController; the
        normal run loop picks it up on the next iteration.
        """
        state = self.state_persistence.load()
        if state is None:
            return

        saved_volume = state.get("volume")
        if isinstance(saved_volume, (int, float)):
            self.volume = float(saved_volume)

        saved_queue = state.get("queue") or []
        restored_queue = [item for item in saved_queue if os.path.isfile(item.get("file", ""))]
        dropped_queue = len(saved_queue) - len(restored_queue)

        now_playing = state.get("now_playing")
        resume_title = None
        if now_playing and os.path.isfile(now_playing.get("filename", "")):
            position = float(now_playing.get("position") or 0.0)
            duration = now_playing.get("duration")
            position_updated_at = now_playing.get("position_updated_at")
            is_paused = bool(now_playing.get("is_paused"))

            if not is_paused and position_updated_at is not None:
                position += max(0.0, time.time() - float(position_updated_at))

            if duration is None or position < float(duration) - 2.0:
                title = self.song_manager.display_name_from_path(
                    now_playing["filename"], remove_youtube_id=True
                )
                resume_item = {
                    "user": now_playing.get("user") or "Pikaraoke",
                    "file": now_playing["filename"],
                    "title": title,
                    "semitones": int(now_playing.get("transpose") or 0),
                }
                restored_queue.insert(0, resume_item)
                self.playback_controller.pending_resume_position = max(0.0, position)
                resume_title = title

        self.queue_manager.queue = restored_queue

        if restored_queue or resume_title:
            summary = f"queue={len(restored_queue)}"
            if resume_title:
                summary += f", resuming '{resume_title}' at {self.playback_controller.pending_resume_position:.1f}s"
            if dropped_queue:
                summary += f", dropped {dropped_queue} missing file(s)"
            logging.info(f"Restored session: {summary}")

    def _persist_state(self) -> None:
        """Snapshot current queue, now-playing, and master volume to disk."""
        pc = self.playback_controller
        now_playing: dict[str, Any] | None = None
        if pc.now_playing_filename:
            now_playing = {
                "filename": pc.now_playing_filename,
                "user": pc.now_playing_user,
                "transpose": pc.now_playing_transpose,
                "duration": pc.now_playing_duration,
                "position": pc.now_playing_position or 0.0,
                "position_updated_at": pc.position_updated_at,
                "is_paused": pc.is_paused,
            }
        self.state_persistence.save(
            {
                "saved_at": time.time(),
                "volume": self.volume,
                "queue": list(self.queue_manager.queue),
                "now_playing": now_playing,
            }
        )

    def reset_now_playing_notification(self) -> None:
        """Clear the current notification."""
        self.now_playing_notification = None

    # ------------------------------------------------------------------
    # song_warning persistence (US-39)
    # ------------------------------------------------------------------

    _SONG_WARNINGS_DB_KEY = "song_warnings"
    _SONG_WARNINGS_MAX = 200

    def _load_song_warnings(self) -> list[dict[str, Any]]:
        try:
            raw = self.db.get_metadata(self._SONG_WARNINGS_DB_KEY)
        except Exception:
            logging.exception("failed to load persisted song_warnings")
            return []
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning("persisted song_warnings not valid JSON; discarding")
            return []
        return data if isinstance(data, list) else []

    def _persist_song_warnings(self) -> None:
        try:
            self.db.set_metadata(self._SONG_WARNINGS_DB_KEY, json.dumps(self._song_warnings))
        except Exception:
            logging.exception("failed to persist song_warnings")

    def _handle_song_warning(self, data: dict[str, Any]) -> None:
        """Forward a song_warning socket event and append it to the persisted log."""
        if self.socketio:
            try:
                self.socketio.emit("song_warning", data, namespace="/")
            except Exception:
                logging.exception("failed to emit song_warning via socketio")
        try:
            entry = dict(data)
            entry.setdefault("timestamp", time.time())
            with self._song_warnings_lock:
                self._song_warnings.append(entry)
                if len(self._song_warnings) > self._SONG_WARNINGS_MAX:
                    del self._song_warnings[: -self._SONG_WARNINGS_MAX]
                self._persist_song_warnings()
        except Exception:
            logging.exception("failed to buffer song_warning")

    def get_song_warnings(self) -> list[dict[str, Any]]:
        """Return a copy of the persisted song_warning buffer (oldest first)."""
        with self._song_warnings_lock:
            return list(self._song_warnings)

    def clear_song_warnings(self) -> None:
        """Wipe the song_warning buffer. Admin-triggered."""
        with self._song_warnings_lock:
            self._song_warnings.clear()
            self._persist_song_warnings()

    def dismiss_song_warnings(self, song: str) -> int:
        """Drop buffered warnings for a specific song. Admin-triggered.

        Returns the number of entries removed so the caller can tell whether
        anything actually matched. Broadcasts ``song_warnings_dismissed`` so
        every pilot + splash wipes its client-side buffer in sync (US-13).
        """
        if not song:
            return 0
        with self._song_warnings_lock:
            before = len(self._song_warnings)
            self._song_warnings = [w for w in self._song_warnings if w.get("song") != song]
            removed = before - len(self._song_warnings)
            if removed:
                self._persist_song_warnings()
        if self.socketio:
            try:
                self.socketio.emit("song_warnings_dismissed", {"song": song}, namespace="/")
            except Exception:
                logging.exception("failed to emit song_warnings_dismissed")
        return removed

    def reset_now_playing(self) -> None:
        """Reset all now playing state to defaults."""
        self.playback_controller.reset_now_playing()
        self.volume = self.preferences.get_or_default("volume")
        self.vocal_volume = self.preferences.get_or_default("vocal_volume")
        self.instrumental_volume = self.preferences.get_or_default("instrumental_volume")
        self.update_now_playing_socket()

    def get_now_playing(self) -> dict[str, Any]:
        """Get the current playback state.

        Returns:
            Dictionary with now playing info, queue preview, and volume.
        """
        queue = self.queue_manager.queue
        next_song = queue[0] if queue else None

        # Get playback state from PlaybackController
        playback_state = self.playback_controller.get_now_playing()

        # Expose per-stem audio URLs only once stems are actually playable
        # (first segment on disk for live Demucs, always true for cache hits).
        # Frontend also gets the same URLs via the `stems_ready` socket event —
        # this poll path is the reconnect/initial-load fallback.
        vocals_url = None
        instrumental_url = None
        stream_url = playback_state.get("now_playing_url")
        if stream_url:
            stream_uid = stream_url.rsplit("/", 1)[-1].split(".", 1)[0]
            stems = self.playback_controller.stream_manager.active_stems.get(stream_uid)
            if stems and stems.ready_event.is_set():
                ext = stems.format  # "wav" or "mp3"
                vocals_url = f"/stream/{stream_uid}/vocals.{ext}"
                instrumental_url = f"/stream/{stream_uid}/instrumental.{ext}"

        return {
            **playback_state,
            "up_next": next_song["title"] if next_song else None,
            "next_user": next_song["user"] if next_song else None,
            "volume": self.volume,
            "vocal_removal": bool(self.preferences.get_or_default("vocal_removal")),
            "vocal_volume": float(self.vocal_volume),
            "instrumental_volume": float(self.instrumental_volume),
            "vocals_url": vocals_url,
            "instrumental_url": instrumental_url,
            "subtitle_offset": float(self.preferences.get_or_default("subtitle_offset")),
        }

    def update_now_playing_socket(self) -> None:
        """Emit now_playing state change via SocketIO."""
        if self.socketio:
            self.socketio.emit("now_playing", self.get_now_playing(), namespace="/")

    def _dispatch_lyrics_fetch_async(self, song_path: str) -> None:
        """Run the lyrics pipeline in a daemon thread (US-10).

        LRCLib + iTunes lookups add up to several seconds per song and
        must not block the shared song_downloaded listener chain — that
        would stall the next download in a multi-queue run.
        """
        threading.Thread(
            target=self.lyrics_service.fetch_and_convert,
            args=(song_path,),
            name=f"lyrics-fetch-{os.path.basename(song_path)}",
            daemon=True,
        ).start()

    def _ensure_fingerprint_on_download(self, song_path: str) -> None:
        """Stamp the audio sha256 right after download so cache invalidation
        cascades before the song's next play (US-15/US-31)."""
        song_id = self.db.get_song_id_by_path(song_path)
        if song_id is None:
            return
        try:
            from pikaraoke.lib.audio_fingerprint import ensure_audio_fingerprint
            from pikaraoke.lib.demucs_processor import resolve_audio_source

            audio_source = resolve_audio_source(song_path)
            ensure_audio_fingerprint(self.db, song_id, audio_source)
        except Exception:
            logging.exception("ensure_audio_fingerprint failed on download for %s", song_path)

    def _on_lyrics_upgraded(self, song_path: str) -> None:
        """Force the splash to reload subtitles when word-level ASS lands mid-song.

        The .ass file on disk is replaced in place; the subtitle URL stays the
        same. Bumping a ``?v=<ts>`` query parameter cache-busts the client's
        HTTP cache and makes splash.js see a new `now_playing_subtitle_url`,
        which triggers its dispose+reinit SubtitlesOctopus path.

        Also refreshes `now_playing_lyrics_source` so the source badge in
        the UI follows the actual source (e.g. "lrclib" → "whisperx" after
        word-level alignment finishes; "whisper" when ASR fallback lands).
        """
        pc = self.playback_controller
        if song_path != pc.now_playing_filename:
            return
        pc.now_playing_lyrics_source = pc.lookup_lyrics_source(song_path)
        base_url = (pc.now_playing_subtitle_url or "").split("?", 1)[0]
        if base_url:
            pc.now_playing_subtitle_url = f"{base_url}?v={int(time.time() * 1000)}"
        self.update_now_playing_socket()

    def run(self) -> None:
        """Main run loop - processes queue and plays songs.

        This method blocks until stop() is called or KeyboardInterrupt.
        """
        logging.debug("Starting PiKaraoke run loop")
        logging.info(f"Connect the player host to: {self.url}/splash")
        self.running = True
        while self.running:
            try:
                # Clean up if playback ended but state wasn't reset
                if (
                    not self.playback_controller.is_playing
                    and self.playback_controller.now_playing is not None
                ):
                    self.reset_now_playing()

                # Prewarm Demucs cache for the next queued song so
                # _prepare_stems hits the cache-hit path. Idempotent: the
                # prewarm function deduplicates by path.
                if self.preferences.get_or_default("vocal_removal") and self.queue_manager.queue:
                    from pikaraoke.lib.demucs_processor import prewarm

                    prewarm(self.queue_manager.queue[0]["file"])

                # Start next song from queue if not currently playing
                if len(self.queue_manager.queue) > 0 and not self.playback_controller.is_playing:
                    self.reset_now_playing()
                    # Splash delay between songs
                    splash_delay = self.preferences.get_or_default("splash_delay")
                    i = 0
                    while i < (splash_delay * 1000):
                        self.handle_run_loop()
                        i += self.loop_interval

                    # Pop song before playback to avoid UI flicker
                    song = self.queue_manager.pop_next()
                    if not song:
                        continue
                    result = self.playback_controller.play_file(
                        song["file"], song["user"], song["semitones"]
                    )

                    if not result.success and result.error:
                        self.log_and_send(result.error, "danger")
                    elif self.playback_controller.pending_resume_position is not None:
                        # Seed the server-side position so splash.js:682 auto-seeks
                        # the video to where the previous run left off.
                        pos = self.playback_controller.pending_resume_position
                        self.playback_controller.pending_resume_position = None
                        self.playback_controller.now_playing_position = pos
                        self.playback_controller.position_updated_at = time.time()
                        self.update_now_playing_socket()

                self.playback_controller.log_output()
                if time.time() - self._last_persist > 2.0:
                    self._persist_state()
                    self._last_persist = time.time()
                self.handle_run_loop()
            except KeyboardInterrupt:
                logging.warning("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False

        # Final snapshot so SIGTERM (watchfiles restart) captures the latest state.
        self._persist_state()
