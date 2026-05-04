"""Subtitle pipeline orchestrator (Phase 1).

After ``song_downloaded`` fires, fan out every supported lyrics source
in parallel via ``LyricsService.fetch_variant_sync`` and persist live
state per (song x source) into the ``subtitle_jobs`` table. The chip UI
on splash + remote (Phase 2) reads this table for live status.

The canonical-tier pipeline (``LyricsService._do_fetch_and_convert``,
which writes ``<stem>.ass`` via the tier gate) keeps running unchanged
in parallel with the orchestrator. The orchestrator only owns the
per-source variant fan-out and the lifecycle state machine — it does
not write the canonical .ass.

Thread safety: each (song, source) job runs on the shared thread pool.
``LyricsService.claim_fetch_in_flight`` dedups when both the on-demand
picker and the orchestrator dispatch the same source for the same
song; the orchestrator records that as ``state='skipped'``,
``error_code='in_flight_dedup'``.
"""

import datetime
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import (
    SUBTITLE_JOB_QUEUED,
    SUBTITLE_JOB_RUNNING,
    SUBTITLE_JOB_SKIPPED,
    SUBTITLE_JOB_SUCCESS,
    VARIANT_FILE_SOURCES,
    KaraokeDatabase,
)
from pikaraoke.lib.lyrics import LyricsService, variant_ass_path

logger = logging.getLogger(__name__)


# Stable order for the chip UI: line-level fast wins on the left, slow
# aligner-driven sources further right, ASR fallback last. Matches the
# picker order in ``karaoke.py:1432`` so the chip row reads consistently
# wherever it surfaces.
DEFAULT_AUTO_SOURCES: tuple[str, ...] = (
    "youtube-vtt",
    "lrclib",
    "lrclib-sync",
    "genius-sync",
    "spotify-sync",
    "tekstowo-sync",
    "AI",
)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class SubtitleOrchestrator:
    """Fan-out coordinator for the per-source subtitle fetch pipeline.

    One instance per Karaoke. Subscribes to ``song_downloaded`` in
    ``attach()``. Each kickoff submits one task per source to a shared
    ``ThreadPoolExecutor`` — Phase 1 keeps the pool simple (single pool
    of size ``max_workers``); per-source semaphores (Spotify rate limit,
    GPU-bound Whisper) are deferred to Phase 3.
    """

    def __init__(
        self,
        lyrics_service: LyricsService,
        events: EventSystem,
        db: KaraokeDatabase | None,
        *,
        sources: tuple[str, ...] = DEFAULT_AUTO_SOURCES,
        max_workers: int = 8,
    ) -> None:
        unknown = [s for s in sources if s not in VARIANT_FILE_SOURCES]
        if unknown:
            raise ValueError(f"SubtitleOrchestrator: unknown sources {unknown}")
        self._lyrics_service = lyrics_service
        self._events = events
        self._db = db
        self._sources = tuple(sources)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="subtitle-orch"
        )
        self._lock = threading.Lock()

    def attach(self) -> None:
        """Wire the orchestrator into the event bus.

        Idempotent in practice — ``EventSystem.on`` appends, so calling
        twice would dispatch twice. Karaoke calls this once at startup.
        """
        self._events.on("song_downloaded", self.kickoff)

    def shutdown(self, wait: bool = True) -> None:
        """Drain in-flight jobs and stop accepting new ones."""
        self._executor.shutdown(wait=wait)

    @property
    def sources(self) -> tuple[str, ...]:
        return self._sources

    # ------------------------------------------------------------------
    # Kickoff
    # ------------------------------------------------------------------

    def kickoff(self, song_path: str) -> None:
        """Queue every configured source for ``song_path``.

        Pre-existing variant files (cache hit on a re-downloaded song)
        are recorded as ``success`` immediately without dispatching a
        worker. Sources that are not pre-existing get a ``queued`` row +
        a worker submission.

        No-op when the DB is not wired (test/CLI shim) — the orchestrator
        is still useful as a dispatch fan-out, but state tracking is
        only meaningful when persisted.
        """
        if self._db is None:
            logger.debug("SubtitleOrchestrator.kickoff: db not wired; skipping %s", song_path)
            return
        try:
            song_id = self._db.get_song_id_by_path(song_path)
        except Exception:
            logger.exception("SubtitleOrchestrator.kickoff: db lookup failed for %s", song_path)
            return
        if song_id is None:
            logger.debug(
                "SubtitleOrchestrator.kickoff: %s not in DB; skipping",
                os.path.basename(song_path),
            )
            return
        for source in self._sources:
            try:
                self._kickoff_one(song_id, song_path, source)
            except Exception:
                logger.exception(
                    "SubtitleOrchestrator: kickoff failed for %s/%s",
                    os.path.basename(song_path),
                    source,
                )

    def _kickoff_one(self, song_id: int, song_path: str, source: str) -> None:
        """Decide whether to queue ``source`` for this song or short-circuit.

        Cache hit (variant file already on disk) -> mark ``success``.
        Otherwise -> mark ``queued`` and submit the fetch.
        """
        if os.path.exists(variant_ass_path(song_path, source)):
            self._record(
                song_id,
                source,
                SUBTITLE_JOB_SUCCESS,
                song_path=song_path,
                tier=self._tier_for(source),
                finished_at=_now_iso(),
            )
            return
        self._record(song_id, source, SUBTITLE_JOB_QUEUED, song_path=song_path)
        self._executor.submit(self._run_one, song_id, song_path, source)

    def _run_one(self, song_id: int, song_path: str, source: str) -> None:
        """Worker body: transition queued -> running -> terminal state."""
        self._record(
            song_id,
            source,
            SUBTITLE_JOB_RUNNING,
            song_path=song_path,
            started_at=_now_iso(),
            increment_attempt=True,
        )
        try:
            result = self._lyrics_service.fetch_variant_sync(song_path, source)
        except Exception as exc:
            logger.exception(
                "SubtitleOrchestrator: fetch_variant_sync crashed for %s/%s",
                os.path.basename(song_path),
                source,
            )
            self._record(
                song_id,
                source,
                "failed",
                song_path=song_path,
                finished_at=_now_iso(),
                error_code="orchestrator_crash",
                error_message=str(exc)[:200],
            )
            self._maybe_emit_no_lyrics_warning(song_id, song_path)
            return
        state = result.get("state", "failed")
        # ``in_flight_dedup`` is not really a failure — the canonical path
        # is doing the work. Surface as 'skipped' so the chip stays neutral
        # rather than amber. The variant file showing up later via
        # ``lyrics_upgraded`` will not flip this row to success on its
        # own; Phase 2 will add a periodic reconciler that scans
        # song_artifacts for new variants and stamps the rows.
        self._record(
            song_id,
            source,
            state if state != "skipped" else SUBTITLE_JOB_SKIPPED,
            song_path=song_path,
            tier=result.get("tier"),
            finished_at=_now_iso(),
            error_code=result.get("error_code"),
            error_message=result.get("error_message"),
        )
        self._maybe_emit_no_lyrics_warning(song_id, song_path)

    def _maybe_emit_no_lyrics_warning(self, song_id: int, song_path: str) -> None:
        """Surface a single ``song_warning`` once every auto source has finished
        and not one landed.

        Avoids the N-warnings-per-song spam pattern (7 sources x failed = 7
        toasts). Quiet when at least one row is ``success`` (some chip is
        ready) or any row is still ``queued`` / ``running`` (verdict not
        in yet).
        """
        if self._db is None:
            return
        try:
            rows = self._db.get_subtitle_jobs(song_id)
        except Exception:
            logger.exception(
                "SubtitleOrchestrator: get_subtitle_jobs failed for %s",
                os.path.basename(song_path),
            )
            return
        # Only consider rows for sources we actually orchestrate; user
        # could have cached a manual variant via the on-demand picker
        # for an unconfigured source — that's not what we're judging.
        my_rows = [r for r in rows if r["source"] in self._sources]
        if not my_rows:
            return
        states = {r["state"] for r in my_rows}
        if SUBTITLE_JOB_QUEUED in states or SUBTITLE_JOB_RUNNING in states:
            return
        if SUBTITLE_JOB_SUCCESS in states:
            return
        # Every configured source is in a terminal non-success state.
        try:
            self._events.emit(
                "song_warning",
                {
                    "message": "No lyrics found",
                    "detail": (
                        "Sprawdziłem wszystkie skonfigurowane źródła napisów "
                        "(LRCLib, Genius, Spotify, Tekstowo, YouTube CC, AI) "
                        "— żadne nie znalazło dopasowania."
                    ),
                    "song": os.path.basename(song_path),
                    "severity": "warning",
                },
            )
        except Exception:
            logger.exception(
                "SubtitleOrchestrator: failed to emit no-lyrics warning for %s",
                os.path.basename(song_path),
            )

    # ------------------------------------------------------------------
    # Persistence + event broadcast
    # ------------------------------------------------------------------

    def _record(
        self,
        song_id: int,
        source: str,
        state: str,
        *,
        song_path: str,
        tier: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_attempt: bool = False,
    ) -> None:
        """Persist the state transition and broadcast ``subtitle_job_update``."""
        if self._db is None:
            return
        try:
            self._db.upsert_subtitle_job(
                song_id,
                source,
                state,
                tier=tier,
                started_at=started_at,
                finished_at=finished_at,
                error_code=error_code,
                error_message=error_message,
                increment_attempt=increment_attempt,
            )
        except Exception:
            logger.exception(
                "SubtitleOrchestrator: failed to persist %s/%s state=%s",
                os.path.basename(song_path),
                source,
                state,
            )
            return
        try:
            self._events.emit(
                "subtitle_job_update",
                {
                    "song_id": song_id,
                    "song": os.path.basename(song_path),
                    "source": source,
                    "state": state,
                    "tier": tier,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
        except Exception:
            logger.exception(
                "SubtitleOrchestrator: failed to emit subtitle_job_update for %s/%s",
                os.path.basename(song_path),
                source,
            )

    def _tier_for(self, source: str) -> str | None:
        from pikaraoke.lib.lyrics import _tier_for_variant_source

        return _tier_for_variant_source(source)
