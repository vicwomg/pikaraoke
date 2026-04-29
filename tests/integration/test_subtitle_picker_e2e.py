"""End-to-end integration tests for the subtitle source picker.

These tests are deliberately heavier than ``tests/unit/test_subtitle_routes.py``:
they wire the real Flask app with the real ``KaraokeDatabase`` (no mock DB)
plus the real ``EventSystem`` and the real subtitle/stream blueprints,
then drive HTTP requests through the actual stack.

The unit tests caught logic bugs but missed two production crashes that
shipped under our noses:

* ``IndexError: No item with that key`` from ``row['subtitle_source_override']``
  on a DB whose ``user_version`` advanced past 7 without the matching ALTER
  (a partially-applied migration / branch schema-number drift).
* ``sqlite3.OperationalError: no such column: lyrics_provenance`` from
  ``get_song_ids_for_realignment`` against the same class of drifted DB.

Both required real DB state to surface. This module reproduces each
drift shape and asserts the runtime self-heals, plus exercises the full
picker flow (POST /subtitle_source -> override persisted -> /subtitle/<id>
serves the right file) end-to-end against a real on-disk DB.
"""

import json
import sqlite3
import threading
from unittest.mock import MagicMock

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import (
    _SCHEMA_V1,
    SUBTITLE_SOURCE_LRCLIB,
    SUBTITLE_SOURCE_OFF,
    KaraokeDatabase,
)

# --------------------------------------------------------------------------
# Test host: a minimal stand-in for Karaoke that owns a REAL ``KaraokeDatabase``
# and the actual Karaoke methods the routes call. This is the same shape
# ``conftest.MockKaraoke`` uses, but with a real DB instead of MagicMock.
# --------------------------------------------------------------------------


class _Host:
    def __init__(self, db: KaraokeDatabase):
        self.db = db
        self.events = EventSystem()
        self.playback_controller = MagicMock()
        self.playback_controller.now_playing_filename = None
        self.playback_controller.now_playing_url = "/stream/uid"
        self.playback_controller.now_playing_subtitle_url = None
        self.playback_controller.is_playing = True
        self.playback_controller.is_paused = False
        self.playback_controller.now_playing_user = "tester"
        self.playback_controller.now_playing_duration = 200
        self.playback_controller.now_playing_transpose = 0
        self.playback_controller.now_playing_position = 0.0
        # Real lyrics-service-shaped object: only the bits the routes
        # touch (the in-flight set + lock + a fetch_variant stub we
        # control from the test).
        self.lyrics_service = _StubLyricsService()
        self.song_manager = MagicMock()
        self.song_manager.filename_from_path = lambda p: p.split("/")[-1] if p else None
        self.song_manager.songs = []
        self.preferences = MagicMock()
        self.preferences.get.return_value = None
        self._socketio = None
        self.now_playing_notification = None
        self.youtube_dl_provider = MagicMock()
        self.run_loop_running = True
        self.running = True

    @property
    def socketio(self):
        return self._socketio

    @socketio.setter
    def socketio(self, value):
        self._socketio = value

    def update_now_playing_socket(self) -> None:
        # No-op for tests; we don't run a real SocketIO loop.
        pass

    def get_song_id_by_path(self, file_path: str | None) -> int | None:
        if not file_path or self.db is None:
            return None
        return self.db.get_song_id_by_path(file_path)


# Bind the real Karaoke methods that the picker payload depends on.
_Host._get_subtitle_sources_for_now_playing = Karaoke._get_subtitle_sources_for_now_playing
_Host._has_local_vtt_file = Karaoke._has_local_vtt_file
_Host._SUBTITLE_STATUS_READY = Karaoke._SUBTITLE_STATUS_READY
_Host._SUBTITLE_STATUS_DOWNLOAD = Karaoke._SUBTITLE_STATUS_DOWNLOAD
_Host._SUBTITLE_STATUS_DOWNLOADING = Karaoke._SUBTITLE_STATUS_DOWNLOADING
_Host._SUBTITLE_STATUS_NA = Karaoke._SUBTITLE_STATUS_NA
_Host._SUBTITLE_SOURCE_ORDER = Karaoke._SUBTITLE_SOURCE_ORDER
_Host._SUBTITLE_SOURCE_LABELS = Karaoke._SUBTITLE_SOURCE_LABELS


class _StubLyricsService:
    """Real-shaped LyricsService stub: owns the in-flight set + lock the
    stream route inspects, and a fetch_variant we can spy on."""

    def __init__(self):
        self._in_flight_variants: set[tuple[str, str]] = set()
        self._in_flight_lock = threading.Lock()
        self.fetch_calls: list[tuple[str, str]] = []
        self._gate = threading.Event()
        self._gate.set()  # default: fetch returns immediately
        # Karaoke._get_subtitle_sources_for_now_playing reads this for
        # the lrclib-sync / AI capability gate. Real LyricsService sets
        # it in __init__ from the configured aligner backend.
        self.has_aligner = True

    def is_fetch_in_flight(self, song_path: str, source: str) -> bool:
        with self._in_flight_lock:
            return (song_path, source) in self._in_flight_variants

    def claim_fetch_in_flight(self, song_path: str, source: str) -> bool:
        key = (song_path, source)
        with self._in_flight_lock:
            if key in self._in_flight_variants:
                return False
            self._in_flight_variants.add(key)
        return True

    def release_fetch_in_flight(self, song_path: str, source: str) -> None:
        with self._in_flight_lock:
            self._in_flight_variants.discard((song_path, source))

    def dispatch_variant_fetch(self, song_path: str, source: str) -> bool:
        # Stub mirrors the real method's contract: claim synchronously,
        # do work in a daemon thread, release the slot when done.
        if not self.claim_fetch_in_flight(song_path, source):
            return False

        def _worker() -> None:
            try:
                self._gate.wait()
                self.fetch_calls.append((song_path, source))
            finally:
                self.release_fetch_in_flight(song_path, source)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def fetch_variant(self, song_path: str, source: str) -> None:
        # Direct fetch_variant entry (used by tests that don't go through
        # the route's dispatch helper). Mirrors the real method's locking.
        if not self.claim_fetch_in_flight(song_path, source):
            return
        try:
            self._gate.wait()
            self.fetch_calls.append((song_path, source))
        finally:
            self.release_fetch_in_flight(song_path, source)


# --------------------------------------------------------------------------
# Fixtures: real DB, real Flask app, real blueprints.
# --------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def host(db):
    return _Host(db)


@pytest.fixture
def app(host, monkeypatch):
    """Real Flask app with subtitle_bp + stream_bp registered, with
    ``current_app`` lookups patched to return the test ``host``."""
    from pikaraoke.routes.stream import stream_bp
    from pikaraoke.routes.subtitle import subtitle_bp

    test_app = Flask(__name__)
    test_app.config["TESTING"] = True
    test_app.register_blueprint(subtitle_bp)
    test_app.register_blueprint(stream_bp)

    monkeypatch.setattr("pikaraoke.routes.subtitle.get_karaoke_instance", lambda: host)
    monkeypatch.setattr("pikaraoke.routes.stream.get_karaoke_instance", lambda: host)
    monkeypatch.setattr("pikaraoke.routes.subtitle.is_admin", lambda: True)
    # broadcast_event is a noop here — we don't run socketio.
    monkeypatch.setattr("pikaraoke.routes.subtitle.broadcast_event", lambda *a, **kw: None)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _insert_song(db: KaraokeDatabase, file_path: str, *, provenance=None) -> int:
    db.insert_songs([{"file_path": file_path, "youtube_id": "abc12345xyz", "format": "mp4"}])
    sid = db.get_song_id_by_path(file_path)
    if provenance is not None:
        db.update_processing_config(sid, lyrics_provenance=provenance)
    return sid


def _post_pin(client, song_id: int, source: str):
    return client.post(
        "/subtitle_source",
        data=json.dumps({"song_id": song_id, "source": source}),
        headers={"Content-Type": "application/json"},
    )


# --------------------------------------------------------------------------
# Drift self-heal tests — these reproduce the two production crashes.
# --------------------------------------------------------------------------


class TestDriftedSchemaSelfHealsAtRuntime:
    """Both production crashes happened on a DB whose ``user_version``
    advanced past a migration without the matching ALTER. The unit test
    in ``tests/unit/test_karaoke_database.py`` covers this at the DB
    layer; this test covers it through the actual code path that
    crashed in production (the run-loop subtitle dispatcher and the
    scanner sweep).
    """

    @staticmethod
    def _build_drifted(db_path: str, *, version: int) -> None:
        """V1 schema + bumped user_version. All later columns are missing."""
        conn = sqlite3.connect(db_path)
        conn.executescript(_SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        conn.close()

    def test_drifted_v6_realignment_sweep_does_not_crash(self, tmp_path):
        """Reproduces ``no such column: lyrics_provenance`` from the
        scanner's stale-alignment sweep."""
        db_path = str(tmp_path / "drifted_v6.db")
        self._build_drifted(db_path, version=6)

        db = KaraokeDatabase(db_path)
        try:
            # This is the exact line that crashed in production.
            ids = db.get_song_ids_for_realignment("any-aligner-model-id")
            assert ids == []
        finally:
            db.close()

    def test_drifted_v7_now_playing_payload_does_not_crash(self, tmp_path, monkeypatch):
        """Reproduces ``IndexError: No item with that key`` on
        ``row['subtitle_source_override']`` from the run-loop's
        now-playing payload builder.

        The migration must self-heal AND the karaoke helper must be
        defensive against rows that, in some other drift scenario, do
        not surface the column on the sqlite3.Row mapping.
        """
        db_path = str(tmp_path / "drifted_v7.db")
        self._build_drifted(db_path, version=7)

        db = KaraokeDatabase(db_path)
        try:
            song_path = str(tmp_path / "Foo---abc12345xyz.mp4")
            (tmp_path / "Foo---abc12345xyz.mp4").write_text("fake")
            sid = _insert_song(db, song_path)

            # Build the payload the run-loop builds. It must not crash
            # and must report a sane shape.
            host = _Host(db)
            host.playback_controller.now_playing_filename = song_path
            sources, override, song_id = host._get_subtitle_sources_for_now_playing()
            assert isinstance(sources, list)
            assert override is None  # no pin set
            assert song_id == sid
        finally:
            db.close()


# --------------------------------------------------------------------------
# Full picker flow against the real DB and real routes.
# --------------------------------------------------------------------------


class TestPickerFlowEndToEnd:
    """POST /subtitle_source persists override; GET /subtitle/<id> reflects
    that override the next time the splash polls."""

    def _song_with_files(self, tmp_path, *, with_variant: bool, with_canonical: bool = True):
        song_path = tmp_path / "Foo---abc12345xyz.mp4"
        song_path.write_text("fake")
        if with_canonical:
            (tmp_path / "Foo---abc12345xyz.ass").write_text("CANONICAL")
        if with_variant:
            (tmp_path / "Foo---abc12345xyz.lrclib.ass").write_text("VARIANT_LRCLIB")
        return str(song_path)

    def test_pin_existing_variant_then_subtitle_route_serves_variant(
        self, client, host, db, tmp_path
    ):
        song_path = self._song_with_files(tmp_path, with_variant=True)
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        # Pin lrclib via the real route — real DB write, real whitelist.
        r = _post_pin(client, sid, SUBTITLE_SOURCE_LRCLIB)
        assert r.status_code == 200
        assert json.loads(r.data) == {"status": "ok"}

        # Real DB has the override.
        assert db.get_subtitle_source_override(sid) == SUBTITLE_SOURCE_LRCLIB

        # Stream route now serves the variant body.
        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        assert r.data == b"VARIANT_LRCLIB"

    def test_pin_off_then_subtitle_route_still_serves_canonical_without_clear(
        self, client, host, db, tmp_path
    ):
        song_path = self._song_with_files(tmp_path, with_variant=False)
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        r = _post_pin(client, sid, SUBTITLE_SOURCE_OFF)
        assert r.status_code == 200
        assert db.get_subtitle_source_override(sid) == SUBTITLE_SOURCE_OFF

        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        assert r.data == b"CANONICAL"
        # ``off`` must NOT be cleared — splash needs the persisted state
        # to suppress Octopus on cold reload.
        assert db.get_subtitle_source_override(sid) == SUBTITLE_SOURCE_OFF

    def test_stale_pin_falls_back_and_clears_when_not_in_flight(self, client, host, db, tmp_path):
        """Pinned source whose variant file is missing AND no fetch is
        in-flight: the route falls back to canonical and clears the
        stale pin so the picker shows ``download`` again."""
        song_path = self._song_with_files(tmp_path, with_variant=False)
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        # Manually pin via DB to simulate "operator picked it long ago,
        # the variant got cleaned up since."
        db.set_subtitle_source_override(sid, SUBTITLE_SOURCE_LRCLIB)

        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        assert r.data == b"CANONICAL"
        # Stale -> cleared.
        assert db.get_subtitle_source_override(sid) is None

    def test_pinned_source_during_in_flight_fetch_is_not_cleared(self, client, host, db, tmp_path):
        """Operator just picked lrclib; variant file is not yet on disk
        because the fetch is still running. The stream route must NOT
        treat that as stale — clearing the pin mid-fetch would
        surface a race-y "your pick reverted" UX. (The user reported
        exactly this: status mismatch where the picker reverted to
        line-level seconds before the word-level variant landed.)
        """
        song_path = self._song_with_files(tmp_path, with_variant=False)
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        # Pin + simulate "fetch is in flight": set the in_flight key
        # without actually running the fetch (the gate is unset so a
        # real fetch_variant call would block — but the route only
        # inspects the set, never invokes the fetch).
        db.set_subtitle_source_override(sid, SUBTITLE_SOURCE_LRCLIB)
        with host.lyrics_service._in_flight_lock:
            host.lyrics_service._in_flight_variants.add((song_path, SUBTITLE_SOURCE_LRCLIB))

        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        assert r.data == b"CANONICAL"
        # Pin survives because the fetch is still in flight.
        assert db.get_subtitle_source_override(sid) == SUBTITLE_SOURCE_LRCLIB

    def test_pin_missing_variant_dispatches_fetch_and_returns_pending(
        self, client, host, db, tmp_path
    ):
        """The full "operator picks AI" path: real DB write, real
        threaded dispatch into the LyricsService stub.
        """
        song_path = self._song_with_files(tmp_path, with_variant=False)
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        r = _post_pin(client, sid, "AI")
        assert r.status_code == 202
        assert json.loads(r.data) == {"status": "pending"}
        assert db.get_subtitle_source_override(sid) == "AI"

        # Wait for the dispatched daemon thread to call into the stub.
        import time

        for _ in range(40):
            if host.lyrics_service.fetch_calls:
                break
            time.sleep(0.025)
        assert (song_path, "AI") in host.lyrics_service.fetch_calls


# --------------------------------------------------------------------------
# Picker payload contract test — the user's "status mismatch" report.
# --------------------------------------------------------------------------


class TestNowPlayingPayloadStatusContract:
    """The user reported: ``LCRLib + sync does not have per-vowel timing
    (they are effectively line-level captions). Okay a minute later they
    appeared. They were available in the ui but not yet fully processed.``

    Status mismatch root cause was that the picker showed ``ready`` for a
    source whose variant ASS hadn't been written yet. This test pins the
    contract: the picker reports ``ready`` ONLY when the variant file
    exists on disk; while the variant is in-flight, status must be
    ``downloading``.
    """

    def test_lrclib_sync_reports_downloading_while_variant_missing(self, host, db, tmp_path):
        song_path = str(tmp_path / "Bar---xyz12345abc.mp4")
        (tmp_path / "Bar---xyz12345abc.mp4").write_text("fake")
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        # In-flight: lrclib-sync variant being fetched.
        with host.lyrics_service._in_flight_lock:
            host.lyrics_service._in_flight_variants.add((song_path, "lrclib-sync"))

        sources, _override, _sid = host._get_subtitle_sources_for_now_playing()
        by_key = {s["source"]: s for s in sources}
        assert by_key["lrclib-sync"]["status"] == "downloading"
        # Picker UI relies on a stable order so the operator sees the
        # same row in the same place.
        assert [s["source"] for s in sources] == list(host._SUBTITLE_SOURCE_ORDER)

    def test_lrclib_sync_reports_ready_when_variant_on_disk(self, host, db, tmp_path):
        song_path = str(tmp_path / "Baz---qrs12345tuv.mp4")
        (tmp_path / "Baz---qrs12345tuv.mp4").write_text("fake")
        (tmp_path / "Baz---qrs12345tuv.lrclib-sync.ass").write_text("V")
        sid = _insert_song(db, song_path, provenance="auto_line")
        host.playback_controller.now_playing_filename = song_path

        sources, _override, returned_sid = host._get_subtitle_sources_for_now_playing()
        assert returned_sid == sid
        by_key = {s["source"]: s for s in sources}
        assert by_key["lrclib-sync"]["status"] == "ready"
