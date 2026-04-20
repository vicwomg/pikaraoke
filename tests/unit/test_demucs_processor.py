"""Tests for the non-torch surface of demucs_processor."""

import threading

import pytest

from pikaraoke.lib import demucs_processor as dp
from pikaraoke.lib.demucs_processor import (
    _encode_in_progress,
    _sep_done_keys,
    _sep_handles,
    acquire_separation,
    cleanup_wavs_if_mp3s_exist,
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


class TestCleanupWavsIfMp3sExist:
    """WAVs must outlive active playback so mid-song restarts still find the
    bytes the browser's range requests expect. Deletion is gated on both
    MP3 siblings being present so an in-flight encode can't strand a half-
    cached song with only WAVs gone.
    """

    def test_removes_wavs_when_both_mp3s_present(self, tmp_path, monkeypatch):
        cache_key = "d" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        (cache_dir / "vocals.mp3").write_bytes(b"mp3_v")
        (cache_dir / "instrumental.mp3").write_bytes(b"mp3_i")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        cleanup_wavs_if_mp3s_exist(cache_key)

        assert not (cache_dir / "vocals.wav").exists()
        assert not (cache_dir / "instrumental.wav").exists()
        assert (cache_dir / "vocals.mp3").exists()
        assert (cache_dir / "instrumental.mp3").exists()

    def test_keeps_wavs_when_only_one_mp3_present(self, tmp_path, monkeypatch):
        cache_key = "e" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        (cache_dir / "vocals.mp3").write_bytes(b"mp3_v")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        cleanup_wavs_if_mp3s_exist(cache_key)

        assert (cache_dir / "vocals.wav").exists()
        assert (cache_dir / "instrumental.wav").exists()

    def test_keeps_wavs_when_no_mp3s(self, tmp_path, monkeypatch):
        cache_key = "f" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        cleanup_wavs_if_mp3s_exist(cache_key)

        assert (cache_dir / "vocals.wav").exists()
        assert (cache_dir / "instrumental.wav").exists()

    def test_idempotent_when_wavs_already_gone(self, tmp_path, monkeypatch):
        cache_key = "0" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.mp3").write_bytes(b"mp3_v")
        (cache_dir / "instrumental.mp3").write_bytes(b"mp3_i")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        cleanup_wavs_if_mp3s_exist(cache_key)  # must not raise


class TestEncodeMp3WavDeletion:
    """Default call path preserves WAVs so the currently-playing song keeps
    serving range requests from them; only the prewarm path opts in to
    immediate deletion.
    """

    def _run_encode_sync(self, monkeypatch, cache_dir, delete_wavs_on_done):
        """Execute encode_mp3_in_background synchronously with a stub ffmpeg
        that writes a smaller MP3 next to each WAV (simulating a real encode).
        """
        mp3_v = cache_dir / "vocals.mp3"
        mp3_i = cache_dir / "instrumental.mp3"

        def fake_run(cmd, check, capture_output):
            # ffmpeg -i <wav> ... -f mp3 <tmp>
            out = cmd[-1]
            open(out, "wb").write(b"mp3_bytes")

            class _R:
                returncode = 0

            return _R()

        import subprocess as _sp

        monkeypatch.setattr(_sp, "run", fake_run)

        captured_target = {}

        class _InlineThread:
            def __init__(self, target=None, daemon=None, **kw):
                captured_target["fn"] = target

            def start(self):
                captured_target["fn"]()

        monkeypatch.setattr(threading, "Thread", _InlineThread)

        cache_key = cache_dir.name
        encode_mp3_in_background(cache_key, delete_wavs_on_done=delete_wavs_on_done)
        assert mp3_v.exists() and mp3_i.exists()

    def test_default_keeps_wavs(self, tmp_path, monkeypatch, clean_encode_state):
        cache_key = "1" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        self._run_encode_sync(monkeypatch, cache_dir, delete_wavs_on_done=False)

        assert (cache_dir / "vocals.wav").exists()
        assert (cache_dir / "instrumental.wav").exists()

    def test_opt_in_removes_wavs(self, tmp_path, monkeypatch, clean_encode_state):
        cache_key = "2" * 64
        cache_dir = tmp_path / cache_key
        cache_dir.mkdir()
        (cache_dir / "vocals.wav").write_bytes(b"wav_v")
        (cache_dir / "instrumental.wav").write_bytes(b"wav_i")
        monkeypatch.setattr(dp, "CACHE_DIR", str(tmp_path))

        self._run_encode_sync(monkeypatch, cache_dir, delete_wavs_on_done=True)

        assert not (cache_dir / "vocals.wav").exists()
        assert not (cache_dir / "instrumental.wav").exists()


class TestPrewarmHooks:
    """Prewarm surfaces progress + ready signals so the front-end seek bar
    can show Demucs state for songs that are being separated before play
    starts (US-7).

    ``prewarm`` spawns a daemon thread; we capture its target and invoke
    it synchronously so we can assert the hooks fire without waiting on
    real Demucs.
    """

    @pytest.fixture
    def restore_hooks(self):
        yield
        dp.set_warning_hook(None)
        dp.set_progress_hook(None)
        dp.set_ready_hook(None)

    def _run_prewarm_sync(self, monkeypatch, file_path):
        """Invoke prewarm's background body on the calling thread."""
        captured = {}

        def fake_thread(target, daemon=None):
            class _T:
                def start(self_inner):
                    captured["target"] = target

            return _T()

        monkeypatch.setattr(dp.threading, "Thread", fake_thread)
        dp.prewarm(file_path)
        target = captured.get("target")
        if target is not None:
            target()

    def test_cache_hit_emits_ready(self, tmp_path, monkeypatch, clean_coordinator, restore_hooks):
        audio = tmp_path / "Song---abcdefghijk.m4a"
        audio.write_text("")
        ready_calls = []
        progress_calls = []
        dp.set_ready_hook(lambda song, key: ready_calls.append((song, key)))
        dp.set_progress_hook(lambda song, p, t: progress_calls.append((song, p, t)))

        # Pretend stems are already on disk for this audio source.
        monkeypatch.setattr(dp, "get_cached_stems", lambda _k: ("v.wav", "i.wav", "wav"))

        self._run_prewarm_sync(monkeypatch, str(audio))

        assert len(ready_calls) == 1
        assert ready_calls[0][0] == "Song---abcdefghijk.m4a"
        # Cache hit short-circuits — no Demucs run, so no progress ticks.
        assert progress_calls == []

    def test_progress_throttles_and_final_tick_fires(
        self, tmp_path, monkeypatch, clean_coordinator, restore_hooks
    ):
        audio = tmp_path / "song.m4a"
        audio.write_text("")
        progress_calls = []
        dp.set_progress_hook(lambda song, p, t: progress_calls.append((p, t)))
        dp.set_ready_hook(lambda *_: None)
        dp.set_warning_hook(lambda *_: None)

        # No cache — force the separation path. Stub out the pieces we
        # don't want to actually exercise.
        monkeypatch.setattr(dp, "get_cached_stems", lambda _k: None)
        monkeypatch.setattr(
            dp, "partial_stem_paths", lambda _k: ("/tmp/v.partial", "/tmp/i.partial")
        )
        monkeypatch.setattr(dp, "finalize_partial_stems", lambda _k: None)
        monkeypatch.setattr(dp, "encode_mp3_in_background", lambda *a, **kw: None)
        monkeypatch.setattr(dp.subprocess, "run", lambda *a, **kw: None)
        monkeypatch.setattr(dp.os, "remove", lambda _p: None)

        def fake_separate(inp, ov, oi, ready_event, progress_callback=None):
            # Two fast ticks (throttled to one) + the final total==processed tick.
            if progress_callback:
                progress_callback(1.0, 10.0)
                progress_callback(2.0, 10.0)  # throttled (within 1s of prior)
                progress_callback(10.0, 10.0)  # final always emits
            return True

        monkeypatch.setattr(dp, "separate_stems", fake_separate)

        self._run_prewarm_sync(monkeypatch, str(audio))

        # Throttling drops the mid-tick; first + final survive.
        assert (1.0, 10.0) in progress_calls
        assert (10.0, 10.0) in progress_calls
        assert (2.0, 10.0) not in progress_calls
