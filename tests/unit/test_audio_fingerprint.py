"""Unit tests for audio_fingerprint.ensure_* helpers."""

import os
from unittest.mock import patch

import pytest

from pikaraoke.lib import audio_fingerprint as af
from pikaraoke.lib.karaoke_database import KaraokeDatabase


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "song.m4a"
    path.write_bytes(b"original audio bytes")
    return str(path)


def _insert_song(db, path="/songs/test.mp4"):
    db.insert_songs([{"file_path": path, "youtube_id": None, "format": "mp4"}])
    return db.get_song_id_by_path(path)


class TestEnsureAudioFingerprint:
    def test_first_call_records_and_registers_cache_dir(self, db, audio_file):
        sid = _insert_song(db)
        # First call: cached_sha is NULL, so we hit the "new" branch.
        with patch.object(af, "_demucs_bits", return_value=("/cache", lambda p: "a" * 64)):
            sha = af.ensure_audio_fingerprint(db, sid, audio_file)

        assert sha == "a" * 64
        row = db.get_song_by_id(sid)
        assert row["audio_sha256"] == "a" * 64
        assert row["audio_size"] == len(b"original audio bytes")

        arts = db.get_artifacts(sid)
        assert any(
            a["role"] == "stems_cache_dir" and a["path"] == os.path.join("/cache", "a" * 64)
            for a in arts
        )

    def test_fast_path_skips_hashing_when_mtime_and_size_match(self, db, audio_file):
        sid = _insert_song(db)
        st = os.stat(audio_file)
        db.update_audio_fingerprint(sid, st.st_mtime, st.st_size, "cafebabe" * 8)

        # get_cache_key must NOT be called on the fast path.
        def boom(_):
            raise AssertionError("hash should not run on fast path")

        with patch.object(af, "_demucs_bits", return_value=("/cache", boom)):
            sha = af.ensure_audio_fingerprint(db, sid, audio_file)

        assert sha == "cafebabe" * 8

    def test_mtime_changed_but_sha_same_updates_metadata_only(self, db, audio_file):
        sid = _insert_song(db)
        # Store a stale mtime/size but with the sha that matches the current bytes.
        db.update_audio_fingerprint(sid, mtime=0.0, size=0, sha256="a" * 64)

        calls = []

        def hash_fn(path):
            calls.append(path)
            return "a" * 64

        with patch.object(af, "_demucs_bits", return_value=("/cache", hash_fn)):
            sha = af.ensure_audio_fingerprint(db, sid, audio_file)

        assert sha == "a" * 64
        assert len(calls) == 1  # did run hash once
        row = db.get_song_by_id(sid)
        # mtime + size were refreshed.
        st = os.stat(audio_file)
        assert row["audio_mtime"] == st.st_mtime
        assert row["audio_size"] == st.st_size

    def test_sha_changed_invalidates_stems_when_sole_owner(self, db, audio_file, tmp_path):
        sid = _insert_song(db)
        old_sha = "a" * 64
        new_sha = "b" * 64
        cache_root = tmp_path / "cache"
        (cache_root / old_sha).mkdir(parents=True)
        (cache_root / old_sha / "vocals.wav").write_bytes(b"x")

        # Fingerprint records the old sha; artifact row points at old cache dir.
        db.update_audio_fingerprint(sid, mtime=0.0, size=0, sha256=old_sha)
        db.upsert_artifacts(sid, [{"role": "stems_cache_dir", "path": str(cache_root / old_sha)}])
        # Auto .ass that should get dropped too.
        ass_path = tmp_path / "test.ass"
        ass_path.write_text("auto")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass_path)}])

        with patch.object(af, "_demucs_bits", return_value=(str(cache_root), lambda p: new_sha)):
            sha = af.ensure_audio_fingerprint(db, sid, audio_file)

        assert sha == new_sha
        assert not (cache_root / old_sha).exists(), "old cache dir should be rmtree'd"
        assert not ass_path.exists(), "auto .ass should be unlinked"

        arts = {(a["role"], a["path"]) for a in db.get_artifacts(sid)}
        assert ("ass_auto", str(ass_path)) not in arts
        assert ("stems_cache_dir", str(cache_root / new_sha)) in arts

    def test_sha_changed_keeps_cache_when_shared(self, db, audio_file, tmp_path):
        s1 = _insert_song(db, "/songs/a.mp4")
        s2 = _insert_song(db, "/songs/b.mp4")
        shared_sha = "a" * 64
        cache_root = tmp_path / "cache"
        (cache_root / shared_sha).mkdir(parents=True)

        # Both songs reference the same sha.
        db.update_audio_fingerprint(s1, 0.0, 0, shared_sha)
        db.update_audio_fingerprint(s2, 0.0, 0, shared_sha)

        new_sha = "b" * 64
        with patch.object(af, "_demucs_bits", return_value=(str(cache_root), lambda p: new_sha)):
            af.ensure_audio_fingerprint(db, s1, audio_file)

        assert (cache_root / shared_sha).exists(), "shared cache must survive"

    def test_preserves_user_ass_on_invalidation(self, db, audio_file, tmp_path):
        sid = _insert_song(db)
        db.update_audio_fingerprint(sid, 0.0, 0, "a" * 64)
        user_ass = tmp_path / "user.ass"
        user_ass.write_text("hand-authored")
        db.upsert_artifacts(sid, [{"role": "ass_user", "path": str(user_ass)}])

        with patch.object(af, "_demucs_bits", return_value=(str(tmp_path), lambda p: "b" * 64)):
            af.ensure_audio_fingerprint(db, sid, audio_file)

        assert user_ass.exists()
        roles = {a["role"] for a in db.get_artifacts(sid)}
        assert "ass_user" in roles

    def test_missing_file_returns_none(self, db):
        sid = _insert_song(db)
        sha = af.ensure_audio_fingerprint(db, sid, "/does/not/exist")
        assert sha is None


class TestEnsureStemsConfig:
    def test_first_time_is_noop(self, db):
        sid = _insert_song(db)
        # NULL demucs_model -> no invalidation, no write.
        assert af.ensure_stems_config(db, sid, "htdemucs") is True
        # Nothing was recorded (per updated semantics, the caller records after
        # stems actually land).
        assert db.get_song_by_id(sid)["demucs_model"] is None

    def test_matching_model_is_noop(self, db):
        sid = _insert_song(db)
        db.update_processing_config(sid, demucs_model="htdemucs")
        assert af.ensure_stems_config(db, sid, "htdemucs") is True

    def test_mismatched_model_invalidates(self, db, tmp_path):
        sid = _insert_song(db)
        sha = "a" * 64
        cache_root = tmp_path / "cache"
        cache = cache_root / sha
        cache.mkdir(parents=True)
        db.update_audio_fingerprint(sid, 0.0, 0, sha)
        db.update_processing_config(sid, demucs_model="htdemucs")
        db.upsert_artifacts(sid, [{"role": "stems_cache_dir", "path": str(cache)}])

        with patch.object(af, "_demucs_bits", return_value=(str(cache_root), None)):
            ok = af.ensure_stems_config(db, sid, "htdemucs_ft")

        assert ok is False
        assert not cache.exists()
        roles = {a["role"] for a in db.get_artifacts(sid)}
        assert "stems_cache_dir" not in roles

    def test_mismatched_model_cascades_to_auto_ass(self, db, tmp_path):
        """US-31: changing demucs_model also drops the auto .ass because
        whisper alignment runs on stems produced by the old model."""
        sid = _insert_song(db)
        sha = "a" * 64
        ass_path = tmp_path / "Foo---abc.ass"
        ass_path.write_text("stale ass")
        db.update_audio_fingerprint(sid, 0.0, 0, sha)
        db.update_processing_config(
            sid, demucs_model="htdemucs", aligner_model="whisperx-base", lyrics_sha="x" * 64
        )
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass_path)}])

        with patch.object(af, "_demucs_bits", return_value=(str(tmp_path / "cache"), None)):
            af.ensure_stems_config(db, sid, "htdemucs_ft")

        # .ass unlinked, row removed, and aligner_model/lyrics_sha cleared.
        assert not ass_path.exists()
        roles = {a["role"] for a in db.get_artifacts(sid)}
        assert "ass_auto" not in roles
        row = db.get_song_by_id(sid)
        assert row["aligner_model"] is None
        assert row["lyrics_sha"] is None


class TestEnsureLyricsConfig:
    def test_first_time_is_noop(self, db):
        sid = _insert_song(db)
        assert af.ensure_lyrics_config(db, sid, "whisperx-base") is True
        assert db.get_song_by_id(sid)["aligner_model"] is None

    def test_matching_model_is_noop(self, db):
        sid = _insert_song(db)
        db.update_processing_config(sid, aligner_model="whisperx-base")
        assert af.ensure_lyrics_config(db, sid, "whisperx-base") is True

    def test_mismatched_model_drops_auto_ass(self, db, tmp_path):
        sid = _insert_song(db)
        db.update_processing_config(sid, aligner_model="whisperx-base")
        ass = tmp_path / "t.ass"
        ass.write_text("auto")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])
        user_ass = tmp_path / "user.ass"
        user_ass.write_text("user")
        db.upsert_artifacts(sid, [{"role": "ass_user", "path": str(user_ass)}])

        ok = af.ensure_lyrics_config(db, sid, "whisperx-large-v3")

        assert ok is False
        assert not ass.exists()
        assert user_ass.exists(), "user .ass must be preserved"


class TestEnsureArtifactFingerprint:
    """US-30: per-artifact sha/size/mtime columns with cheap-refresh semantics."""

    def test_first_call_records_fingerprint(self, db, tmp_path):
        sid = _insert_song(db)
        ass = tmp_path / "Foo.ass"
        ass.write_text("line lyrics")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])

        sha = af.ensure_artifact_fingerprint(db, sid, str(ass))

        assert sha is not None and len(sha) == 64
        row = [a for a in db.get_artifacts(sid) if a["path"] == str(ass)][0]
        assert row["sha256"] == sha
        assert row["size"] == len("line lyrics")
        assert row["mtime"] is not None

    def test_stat_match_skips_recompute(self, db, tmp_path):
        """When mtime+size match, return cached sha without reading the file."""
        sid = _insert_song(db)
        ass = tmp_path / "Foo.ass"
        ass.write_text("content")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])

        af.ensure_artifact_fingerprint(db, sid, str(ass))

        with patch.object(af, "_hash_file_sha256", side_effect=AssertionError("should skip")):
            sha = af.ensure_artifact_fingerprint(db, sid, str(ass))
        assert sha is not None and len(sha) == 64

    def test_content_change_refreshes(self, db, tmp_path):
        sid = _insert_song(db)
        ass = tmp_path / "Foo.ass"
        ass.write_text("original")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])
        original_sha = af.ensure_artifact_fingerprint(db, sid, str(ass))

        ass.write_text("totally different content that changes size")
        # Advance mtime so the stat-match branch triggers recompute.
        future = os.path.getmtime(str(ass)) + 5
        os.utime(str(ass), (future, future))

        new_sha = af.ensure_artifact_fingerprint(db, sid, str(ass))
        assert new_sha is not None and new_sha != original_sha

    def test_missing_file_returns_none(self, db):
        sid = _insert_song(db)
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": "/does/not/exist.ass"}])
        assert af.ensure_artifact_fingerprint(db, sid, "/does/not/exist.ass") is None

    def test_unknown_artifact_returns_none(self, db, tmp_path):
        """An artifact not registered for this song is a no-op (no row to update)."""
        sid = _insert_song(db)
        stranger = tmp_path / "stranger.ass"
        stranger.write_text("x")
        assert af.ensure_artifact_fingerprint(db, sid, str(stranger)) is None


class TestLyricsShaClearedOnAudioChange:
    """US-31 waterfall: audio sha change must invalidate lyrics_sha so the
    next pipeline run re-fetches LRCLib. Otherwise ``ensure_lyrics_config``
    sees a matching cached sha and skips re-fetch even though the source
    audio changed.
    """

    def test_audio_sha_change_clears_lyrics_sha(self, db, audio_file, tmp_path):
        sid = _insert_song(db)
        cache_root = tmp_path / "cache"
        (cache_root / ("a" * 64)).mkdir(parents=True)
        db.update_audio_fingerprint(sid, 0.0, 0, "a" * 64)
        db.update_processing_config(sid, lyrics_sha="oldlyrics" * 8, aligner_model="whisperx-base")
        # Add an auto .ass so _invalidate_auto_ass actually runs its loop.
        ass = tmp_path / "t.ass"
        ass.write_text("auto")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])

        with patch.object(af, "_demucs_bits", return_value=(str(cache_root), lambda p: "b" * 64)):
            af.ensure_audio_fingerprint(db, sid, audio_file)

        row = db.get_song_by_id(sid)
        assert row["lyrics_sha"] is None, "lyrics_sha must clear so LRCLib is re-fetched"
        assert row["aligner_model"] is None, "aligner_model must clear too"

    def test_lyrics_config_invalidation_also_clears_sha(self, db, tmp_path):
        sid = _insert_song(db)
        db.update_processing_config(sid, aligner_model="whisperx-base", lyrics_sha="abc" * 20)
        ass = tmp_path / "t.ass"
        ass.write_text("auto")
        db.upsert_artifacts(sid, [{"role": "ass_auto", "path": str(ass)}])

        af.ensure_lyrics_config(db, sid, "whisperx-large-v3")

        row = db.get_song_by_id(sid)
        assert row["lyrics_sha"] is None
        assert row["aligner_model"] is None
