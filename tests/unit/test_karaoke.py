"""Unit tests for module-level Karaoke helpers.

The DB-driven stale-alignment sweep is exercised by calling
``Karaoke._invalidate_stale_alignments_from_db`` against a hand-rolled stub
that exposes only the attributes the method reads (``_aligner_instance``,
``db``). The DB itself is a real ``KaraokeDatabase`` against a tmp file so
the SQL query and ``invalidate_auto_ass`` round-trip are covered end-to-end.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from pikaraoke.karaoke import Karaoke, _BackfillScheduler
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.song_manager import ASS_AUTO_ROLE


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


def _make_song(db, file_path, *, provenance, aligner_model=None, ass_path=None):
    """Insert a song + an ass_auto artifact and stamp its provenance."""
    db.insert_songs([{"file_path": file_path, "youtube_id": None, "format": "mp4"}])
    sid = db.get_song_id_by_path(file_path)
    if ass_path is not None:
        db.upsert_artifacts(sid, [{"role": ASS_AUTO_ROLE, "path": ass_path}])
    db.update_processing_config(sid, aligner_model=aligner_model, lyrics_provenance=provenance)
    return sid


class TestInvalidateStaleAlignmentsFromDb:
    def test_invalidates_only_stale_word_level(self, tmp_path, db):
        # Five rows mixing every classification we care about.
        stale = tmp_path / "stale.ass"
        stale.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        legacy = tmp_path / "legacy.ass"
        legacy.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        current = tmp_path / "current.ass"
        current.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        line = tmp_path / "line.ass"
        line.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        user = tmp_path / "user.ass"
        user.write_text("[Script Info]\nTitle: My Custom\n", encoding="utf-8")

        stale_id = _make_song(
            db,
            str(tmp_path / "stale.mp4"),
            provenance="auto_word",
            aligner_model="old-model",
            ass_path=str(stale),
        )
        legacy_id = _make_song(
            db,
            str(tmp_path / "legacy.mp4"),
            provenance="auto_word",
            aligner_model=None,
            ass_path=str(legacy),
        )
        current_id = _make_song(
            db,
            str(tmp_path / "current.mp4"),
            provenance="auto_word",
            aligner_model="new-model",
            ass_path=str(current),
        )
        line_id = _make_song(
            db,
            str(tmp_path / "line.mp4"),
            provenance="auto_line",
            aligner_model=None,
            ass_path=str(line),
        )
        user_id = _make_song(
            db,
            str(tmp_path / "user.mp4"),
            provenance="user",
            aligner_model=None,
            ass_path=str(user),
        )

        stub = SimpleNamespace(
            _aligner_instance=SimpleNamespace(model_id="new-model"),
            db=db,
        )
        Karaoke._invalidate_stale_alignments_from_db(stub)

        # Stale + legacy word-level files: deleted, artifact rows dropped,
        # aligner_model cleared.
        assert not stale.exists()
        assert not legacy.exists()
        assert db.get_artifacts(stale_id) == []
        assert db.get_artifacts(legacy_id) == []
        assert db.get_song_by_id(stale_id)["aligner_model"] is None
        assert db.get_song_by_id(legacy_id)["aligner_model"] is None

        # Current word-level + line-level + user-owned: untouched.
        assert current.exists()
        assert line.exists()
        assert user.exists()
        current_arts = {a["role"] for a in db.get_artifacts(current_id)}
        assert current_arts == {ASS_AUTO_ROLE}
        assert db.get_song_by_id(current_id)["aligner_model"] == "new-model"
        assert {a["role"] for a in db.get_artifacts(line_id)} == {ASS_AUTO_ROLE}
        assert {a["role"] for a in db.get_artifacts(user_id)} == {ASS_AUTO_ROLE}

    def test_no_op_when_aligner_disabled(self, tmp_path, db):
        ass = tmp_path / "stale.ass"
        ass.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        sid = _make_song(
            db,
            str(tmp_path / "stale.mp4"),
            provenance="auto_word",
            aligner_model="old-model",
            ass_path=str(ass),
        )

        stub = SimpleNamespace(_aligner_instance=None, db=db)
        Karaoke._invalidate_stale_alignments_from_db(stub)

        assert ass.exists()
        assert {a["role"] for a in db.get_artifacts(sid)} == {ASS_AUTO_ROLE}

    def test_no_op_when_aligner_lacks_model_id(self, tmp_path, db):
        ass = tmp_path / "stale.ass"
        ass.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        sid = _make_song(
            db,
            str(tmp_path / "stale.mp4"),
            provenance="auto_word",
            aligner_model="old-model",
            ass_path=str(ass),
        )

        stub = SimpleNamespace(_aligner_instance=SimpleNamespace(), db=db)
        Karaoke._invalidate_stale_alignments_from_db(stub)

        assert ass.exists()
        assert {a["role"] for a in db.get_artifacts(sid)} == {ASS_AUTO_ROLE}

    def test_idempotent_on_repeat_calls(self, tmp_path, db):
        ass = tmp_path / "stale.ass"
        ass.write_text("[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n", encoding="utf-8")
        sid = _make_song(
            db,
            str(tmp_path / "stale.mp4"),
            provenance="auto_word",
            aligner_model="old-model",
            ass_path=str(ass),
        )
        stub = SimpleNamespace(
            _aligner_instance=SimpleNamespace(model_id="new-model"),
            db=db,
        )
        Karaoke._invalidate_stale_alignments_from_db(stub)
        # invalidate_auto_ass also clears lyrics_provenance, so the row drops
        # out of the sweep query - the SELECT returns 0 ids on the next call.
        assert db.get_song_by_id(sid)["lyrics_provenance"] is None
        assert db.get_song_ids_for_realignment("new-model") == []
        # Second call is a genuine no-op (no rows match).
        Karaoke._invalidate_stale_alignments_from_db(stub)


class TestBackfillScheduler:
    def test_processes_all_when_idle(self):
        seen: list[int] = []
        scheduler = _BackfillScheduler(
            db=object(),
            invalidate=lambda _db, sid: seen.append(sid),
            is_idle=lambda: True,
        )
        processed = scheduler.run([1, 2, 3])
        assert processed == 3
        assert seen == [1, 2, 3]

    def test_stops_on_explicit_stop(self):
        seen: list[int] = []
        scheduler = _BackfillScheduler(
            db=object(),
            invalidate=lambda _db, sid: seen.append(sid),
            is_idle=lambda: True,
        )
        scheduler.stop()
        scheduler.run([1, 2, 3])
        assert seen == []

    def test_logs_and_continues_on_invalidate_error(self, caplog):
        seen: list[int] = []

        def invalidate(_db, sid):
            if sid == 2:
                raise RuntimeError("boom")
            seen.append(sid)

        scheduler = _BackfillScheduler(
            db=object(),
            invalidate=invalidate,
            is_idle=lambda: True,
        )
        with caplog.at_level("ERROR"):
            processed = scheduler.run([1, 2, 3])
        # Failed song doesn't count toward processed, but the loop
        # continues so song 3 still runs.
        assert processed == 2
        assert seen == [1, 3]

    def test_pauses_during_active_playback(self):
        # Idle flips True → False → True; the scheduler must wait for
        # idle before processing the second song. With a 0.05s pause
        # poll override it does so within ~150ms.
        seen: list[int] = []
        idle_state = {"value": True}

        def is_idle() -> bool:
            return idle_state["value"]

        scheduler = _BackfillScheduler(
            db=object(),
            invalidate=lambda _db, sid: seen.append(sid),
            is_idle=is_idle,
        )
        scheduler._PAUSE_POLL_S = 0.02

        # Block playback after the first song lands.
        original_invalidate = scheduler._invalidate

        def gating_invalidate(db, sid):
            original_invalidate(db, sid)
            if sid == 1:
                idle_state["value"] = False

        scheduler._invalidate = gating_invalidate

        result: dict[str, int] = {}
        thread = threading.Thread(
            target=lambda: result.setdefault("processed", scheduler.run([1, 2])),
            daemon=True,
        )
        thread.start()

        # Wait long enough for the first song to land + the loop to
        # observe is_idle=False, then verify song 2 is still pending.
        time.sleep(0.1)
        assert seen == [1], f"second song fired during playback: {seen}"

        # Re-enable idle; the scheduler must resume.
        idle_state["value"] = True
        thread.join(timeout=2.0)
        assert seen == [1, 2]
        assert result["processed"] == 2

    def test_stop_breaks_pause_loop(self):
        scheduler = _BackfillScheduler(
            db=object(),
            invalidate=lambda _db, _sid: None,
            is_idle=lambda: False,
        )
        scheduler._PAUSE_POLL_S = 0.02

        thread = threading.Thread(
            target=lambda: scheduler.run([1, 2]), daemon=True
        )
        thread.start()
        time.sleep(0.05)
        scheduler.stop()
        thread.join(timeout=1.0)
        assert not thread.is_alive(), "stop() did not unblock the pause loop"
