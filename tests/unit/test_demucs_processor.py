"""Tests for the non-torch surface of demucs_processor."""

import threading

import pytest

from pikaraoke.lib import demucs_processor as dp
from pikaraoke.lib.demucs_processor import (
    _encode_in_progress,
    _sep_done_keys,
    _sep_handles,
    acquire_separation,
    encode_mp3_in_background,
    release_separation,
    resolve_audio_source,
)


@pytest.fixture
def clean_coordinator():
    """Reset the global separation coordinator state between tests."""
    _sep_handles.clear()
    _sep_done_keys.clear()
    yield
    _sep_handles.clear()
    _sep_done_keys.clear()


class TestResolveAudioSource:
    """Prewarm uses the sibling .m4a when it exists; otherwise the input.

    These paths feed both the cache-key SHA256 and the ffmpeg extract
    step, so a stable answer across download- and play-time invocations
    is the whole point.
    """

    def test_prefers_sibling_m4a(self, tmp_path):
        video = tmp_path / "song.mp4"
        audio = tmp_path / "song.m4a"
        video.write_text("")
        audio.write_text("")
        assert resolve_audio_source(str(video)) == str(audio)

    def test_no_sibling_returns_input(self, tmp_path):
        video = tmp_path / "song.mp4"
        video.write_text("")
        assert resolve_audio_source(str(video)) == str(video)

    def test_audio_input_returned_unchanged(self, tmp_path):
        audio = tmp_path / "song.m4a"
        audio.write_text("")
        assert resolve_audio_source(str(audio)) == str(audio)

    def test_mp3_input_returned_unchanged(self, tmp_path):
        # mp3 callers bypass sibling lookup — the file itself is audio.
        mp3 = tmp_path / "song.mp3"
        mp3.write_text("")
        assert resolve_audio_source(str(mp3)) == str(mp3)

    def test_webm_without_sibling_returns_input(self, tmp_path):
        # We scope sibling resolution to mp4 callers in FileResolver, but
        # the helper itself falls through for any video container.
        video = tmp_path / "song.webm"
        video.write_text("")
        assert resolve_audio_source(str(video)) == str(video)


class TestSeparationCoordinator:
    """Per-song dedup coordinator. Prevents download_manager, lyrics, and
    stream_manager from running three parallel demucs separations that race
    on the same .partial files.
    """

    def test_first_caller_becomes_owner(self, clean_coordinator):
        is_owner, handle = acquire_separation("/s/song.m4a")
        assert is_owner is True
        assert handle.ready_event.is_set() is False
        assert handle.done_event.is_set() is False

    def test_second_caller_shares_owner_handle(self, clean_coordinator):
        _, owner_handle = acquire_separation("/s/song.m4a")
        is_owner, waiter_handle = acquire_separation("/s/song.m4a")
        assert is_owner is False
        # Waiter gets the SAME handle so it sees ready_event / done_event
        # fire at the same instant the owner finishes.
        assert waiter_handle is owner_handle

    def test_release_unblocks_waiters(self, clean_coordinator):
        _, owner_handle = acquire_separation("/s/song.m4a")
        _, waiter_handle = acquire_separation("/s/song.m4a")
        release_separation("/s/song.m4a", success=True)
        assert owner_handle.ready_event.is_set()
        assert owner_handle.done_event.is_set()
        assert waiter_handle.success is True

    def test_release_on_failure_still_unblocks(self, clean_coordinator):
        # Owner crash must not leave waiters stuck forever.
        _, owner_handle = acquire_separation("/s/song.m4a")
        release_separation("/s/song.m4a", success=False)
        assert owner_handle.done_event.is_set()
        assert owner_handle.success is False

    def test_cache_done_returns_preset_handle(self, clean_coordinator):
        # After a successful release, later acquires report non-owner with
        # pre-set events so waiters never block on already-cached work.
        _, _ = acquire_separation("/s/song.m4a")
        release_separation("/s/song.m4a", success=True)
        is_owner, handle = acquire_separation("/s/song.m4a")
        assert is_owner is False
        assert handle.ready_event.is_set()
        assert handle.done_event.is_set()
        assert handle.success is True

    def test_failed_run_allows_retry(self, clean_coordinator):
        # A failed separation should not poison the coordinator — the next
        # caller must be allowed to try again rather than get a stale
        # "success" handle.
        _, _ = acquire_separation("/s/song.m4a")
        release_separation("/s/song.m4a", success=False)
        is_owner, _ = acquire_separation("/s/song.m4a")
        assert is_owner is True

    def test_distinct_sources_are_independent(self, clean_coordinator):
        is_owner_a, _ = acquire_separation("/s/a.m4a")
        is_owner_b, _ = acquire_separation("/s/b.m4a")
        assert is_owner_a is True
        assert is_owner_b is True


@pytest.fixture
def clean_encode_state():
    _encode_in_progress.clear()
    yield
    _encode_in_progress.clear()


class TestEncodeMp3Dedup:
    """encode_mp3_in_background must dedup concurrent callers: three entry
    points (prewarm, stream_manager owner, attached waiter) can invoke it
    for the same cache_key and must not race two ffmpeg processes on the
    same .partial file.
    """

    def test_second_call_noops_while_first_in_flight(
        self, tmp_path, monkeypatch, clean_encode_state
    ):
        cache_key = "a" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"fake")
        (cache_dir / "instrumental.wav").write_bytes(b"fake")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        # Simulate a first call still running by marking the key as in
        # progress without spawning the worker thread.
        _encode_in_progress.add(cache_key)

        spawned = []
        original_thread = threading.Thread

        def tracking_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            spawned.append(t)
            return t

        monkeypatch.setattr(threading, "Thread", tracking_thread)
        encode_mp3_in_background(cache_key)
        assert spawned == []

    def test_noop_when_mp3s_already_exist(self, tmp_path, monkeypatch, clean_encode_state):
        cache_key = "b" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"fake")
        (cache_dir / "instrumental.wav").write_bytes(b"fake")
        (cache_dir / "vocals.mp3").write_bytes(b"fake")
        (cache_dir / "instrumental.mp3").write_bytes(b"fake")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        spawned = []
        monkeypatch.setattr(
            threading, "Thread", lambda *a, **kw: spawned.append((a, kw)) or threading.Event()
        )
        encode_mp3_in_background(cache_key)
        assert spawned == []
        # And the dedup set is not polluted when the function early-exits.
        assert cache_key not in _encode_in_progress

    def test_noop_when_wavs_missing(self, tmp_path, monkeypatch, clean_encode_state):
        cache_key = "c" * 64
        (tmp_path / cache_key).mkdir()
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))
        # No wavs, no mp3s.

        spawned = []
        monkeypatch.setattr(
            threading, "Thread", lambda *a, **kw: spawned.append((a, kw)) or threading.Event()
        )
        encode_mp3_in_background(cache_key)
        assert spawned == []
        assert cache_key not in _encode_in_progress
