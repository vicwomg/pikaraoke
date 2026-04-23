"""Unit tests for the US-43 Tier 2a Whisper language-ID raw-audio probe."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pikaraoke.lib.lyrics_audio_probe import (
    _FALLBACK_OFFSET,
    _PRIMARY_OFFSET,
    _PROBE_WINDOW_S,
    _SAMPLE_RATE,
    _cache_key,
    probe_language,
    read_cached_verdict,
)

# --- shared fixtures ---------------------------------------------------


class _FakeCache:
    """In-memory stand-in for ``KaraokeDatabase.get_metadata/set_metadata``."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str) -> None:
        self.store[key] = value


@pytest.fixture
def cache() -> _FakeCache:
    return _FakeCache()


@pytest.fixture
def audio():
    """5-minute stub audio: plain float32 buffer at 16 kHz.

    Content doesn't matter — the fake Whisper model never inspects it,
    it just lets us assert which slice was handed in.
    """
    return np.zeros(int(300 * _SAMPLE_RATE), dtype=np.float32)


def _decode_audio(audio_arr):
    """Return a ``decode_audio_fn`` stub that yields the fixture array."""

    def _fn(_path, *, sampling_rate=_SAMPLE_RATE):
        assert sampling_rate == _SAMPLE_RATE
        return audio_arr

    return _fn


def _model(verdicts: list[tuple[str, float]]) -> MagicMock:
    """Build a fake WhisperModel whose ``detect_language`` pops from ``verdicts``."""
    mock = MagicMock()
    mock.detect_language.side_effect = [(lang, conf, [(lang, conf)]) for lang, conf in verdicts]
    return mock


# --- cache helpers ------------------------------------------------------


class TestReadCachedVerdict:
    def test_miss_returns_false(self, cache):
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert (lang, hit) == (None, False)

    def test_hit_with_language(self, cache):
        cache.set(_cache_key("sha123"), json.dumps({"lang": "pl", "conf": 0.9}))
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert lang == "pl" and hit is True

    def test_hit_with_negative_verdict_is_still_a_hit(self, cache):
        """Cached "probed, inconclusive" must short-circuit re-probing."""
        cache.set(_cache_key("sha123"), json.dumps({"lang": None, "conf": None}))
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert lang is None and hit is True

    def test_corrupt_json_is_treated_as_miss(self, cache):
        cache.set(_cache_key("sha123"), "{not-json")
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert (lang, hit) == (None, False)


# --- probe_language core paths -----------------------------------------


class TestProbeLanguage:
    def test_cache_hit_skips_whisper_entirely(self, cache):
        cache.set(_cache_key("sha123"), json.dumps({"lang": "pl", "conf": 0.9}))
        model = MagicMock()
        get_model = MagicMock(return_value=model)

        result = probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="sha123",
            duration_seconds=200.0,
            get_model=get_model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=MagicMock(),
        )

        assert result == "pl"
        # Neither the model nor decode_audio should be touched on cache hit.
        get_model.assert_not_called()
        model.detect_language.assert_not_called()

    def test_cache_hit_with_none_language_is_honored(self, cache):
        cache.set(_cache_key("sha123"), json.dumps({"lang": None, "conf": None}))
        get_model = MagicMock()
        result = probe_language(
            audio_path="/tmp/s.m4a",
            audio_sha256="sha123",
            duration_seconds=200.0,
            get_model=get_model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=MagicMock(),
        )
        assert result is None
        get_model.assert_not_called()

    def test_high_confidence_single_window_accepts_and_caches(self, cache, audio):
        model = _model([("pl", 0.87)])
        result = probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result == "pl"
        # Only one window should have been probed (confidence >= 0.5).
        assert model.detect_language.call_count == 1
        # Cached with the real confidence so later reads short-circuit.
        cached = json.loads(cache.get(_cache_key("abc")))
        assert cached == {"lang": "pl", "conf": pytest.approx(0.87)}

    def test_window_is_centred_at_50_percent_of_duration(self, cache, audio):
        """The primary window's start sample should match 50% - 15s."""
        model = _model([("en", 0.9)])
        probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        called_with = model.detect_language.call_args.kwargs["audio"]
        assert called_with.shape[0] == int(_PROBE_WINDOW_S * _SAMPLE_RATE)
        # Derive where we sliced out of the 5-minute buffer to verify the
        # 50%-centre contract (150s - 15s = 135s from the start).
        expected_samples = int(_PROBE_WINDOW_S * _SAMPLE_RATE)
        primary_centre_s = 300.0 * _PRIMARY_OFFSET
        expected_start = int(primary_centre_s * _SAMPLE_RATE) - expected_samples // 2
        np.testing.assert_array_equal(
            called_with,
            audio[expected_start : expected_start + expected_samples],
        )

    def test_low_confidence_triggers_fallback_window(self, cache, audio):
        """Two disagreeing low-confidence windows defer to Tier 3."""
        model = _model([("en", 0.3), ("pl", 0.4)])
        result = probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result is None
        assert model.detect_language.call_count == 2
        # Inconclusive outcome is cached so we don't re-probe next boot.
        cached = json.loads(cache.get(_cache_key("abc")))
        assert cached == {"lang": None, "conf": None}

    def test_majority_vote_accepts_two_agreeing_low_confidence_windows(self, cache, audio):
        """Instrumental-heavy guard: low conf is fine IF both windows agree."""
        model = _model([("pl", 0.35), ("pl", 0.42)])
        result = probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result == "pl"
        assert model.detect_language.call_count == 2
        cached = json.loads(cache.get(_cache_key("abc")))
        # Cache records the MIN of the two confidences (the weaker one
        # is the relevant lower bound for any downstream trust check).
        assert cached["lang"] == "pl"
        assert cached["conf"] == pytest.approx(0.35)

    def test_fallback_window_offset_is_30_percent(self, cache, audio):
        """Confirm the fallback window centres at 30% of duration."""
        model = _model([("en", 0.2), ("pl", 0.2)])
        probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert model.detect_language.call_count == 2
        fallback_slice = model.detect_language.call_args_list[1].kwargs["audio"]
        window_samples = int(_PROBE_WINDOW_S * _SAMPLE_RATE)
        fallback_centre_s = 300.0 * _FALLBACK_OFFSET
        expected_start = int(fallback_centre_s * _SAMPLE_RATE) - window_samples // 2
        np.testing.assert_array_equal(
            fallback_slice,
            audio[expected_start : expected_start + window_samples],
        )

    def test_model_unavailable_returns_none_no_cache_write(self, cache, audio):
        result = probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: None,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result is None
        # No cache write: the model might be available on the next boot
        # (user installed faster-whisper, flipped the opt-out), and we
        # don't want a transient env issue to lock in a negative.
        assert cache.store == {}

    def test_decode_failure_returns_none(self, cache):
        def _boom(_path, **_kw):
            raise OSError("nope")

        model = MagicMock()
        result = probe_language(
            audio_path="/tmp/song.m4a",
            audio_sha256="abc",
            duration_seconds=300.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_boom,
        )
        assert result is None
        model.detect_language.assert_not_called()
        # Decode failure might be transient (disk IO hiccup); don't
        # poison the cache with a negative verdict.
        assert cache.store == {}

    def test_short_audio_uses_whatever_is_available(self, cache):
        """A 10-second clip still gets probed (no silent skip)."""
        short = np.zeros(10 * _SAMPLE_RATE, dtype=np.float32)
        model = _model([("pl", 0.9)])
        result = probe_language(
            audio_path="/tmp/short.m4a",
            audio_sha256="short",
            duration_seconds=10.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(short),
        )
        assert result == "pl"
        called_with = model.detect_language.call_args.kwargs["audio"]
        # Short clips use the entire buffer we have, not a slice.
        assert called_with.shape[0] == short.shape[0]

    def test_empty_audio_returns_none_and_caches_negative(self, cache):
        empty = np.zeros(0, dtype=np.float32)
        model = MagicMock()
        result = probe_language(
            audio_path="/tmp/empty.m4a",
            audio_sha256="empty",
            duration_seconds=0.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(empty),
        )
        assert result is None
        model.detect_language.assert_not_called()
        cached = json.loads(cache.get(_cache_key("empty")))
        assert cached == {"lang": None, "conf": None}

    def test_language_subtag_is_normalised(self, cache, audio):
        """``en-US`` must collapse to ``en`` before persisting."""
        model = _model([("en-US", 0.92)])
        result = probe_language(
            audio_path="/tmp/s.m4a",
            audio_sha256="sha",
            duration_seconds=200.0,
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result == "en"


# --- LyricsService wire-in ---------------------------------------------


class TestLyricsServiceTier2aWiring:
    """Tier 2a should fire only on Tier 1 miss; never on Tier 1 consensus."""

    def test_probe_skipped_when_tier1_reaches_consensus(self, tmp_path):
        from pikaraoke.lib.events import EventSystem
        from pikaraoke.lib.karaoke_database import KaraokeDatabase
        from pikaraoke.lib.lyrics import LyricsService
        from pikaraoke.lib.lyrics_language_classifier import ConsensusVerdict

        db = KaraokeDatabase(str(tmp_path / "t.db"))
        try:
            song_path = str(tmp_path / "song.mp4")
            open(song_path, "w").close()
            db.insert_songs([{"file_path": song_path, "youtube_id": None, "format": "mp4"}])
            svc = LyricsService(str(tmp_path), EventSystem(), db=db)
            verdict = ConsensusVerdict(language="pl", agreement=2, winning_source="itunes_text")
            with (
                patch(
                    "pikaraoke.lib.lyrics._classify_language",
                    return_value=([], verdict),
                ),
                patch("pikaraoke.lib.lyrics._probe_audio_language") as mock_probe,
            ):
                svc._run_language_classifier(song_path, None)
            mock_probe.assert_not_called()
        finally:
            db.close()

    def test_probe_fires_on_tier1_miss_and_persists(self, tmp_path):
        from pikaraoke.lib.events import EventSystem
        from pikaraoke.lib.karaoke_database import KaraokeDatabase
        from pikaraoke.lib.lyrics import LyricsService

        db = KaraokeDatabase(str(tmp_path / "t.db"))
        try:
            song_path = str(tmp_path / "song.mp4")
            # Create both the mp4 and its audio sibling so resolve_audio_source
            # finds something to fingerprint.
            open(song_path, "w").close()
            (tmp_path / "song.m4a").write_bytes(b"\x00" * 1024)
            db.insert_songs([{"file_path": song_path, "youtube_id": None, "format": "mp4"}])
            svc = LyricsService(str(tmp_path), EventSystem(), db=db)
            with (
                patch(
                    "pikaraoke.lib.lyrics._classify_language",
                    return_value=([], None),
                ),
                patch(
                    "pikaraoke.lib.lyrics._probe_audio_language",
                    return_value="pl",
                ) as mock_probe,
            ):
                svc._run_language_classifier(song_path, None)
            mock_probe.assert_called_once()
            song_id = db.get_song_id_by_path(song_path)
            row = db.get_song_by_id(song_id)
            assert row["language"] == "pl"
            sources = db.get_metadata_sources(song_id)
            assert sources["language"] == "whisper_probe_raw"
        finally:
            db.close()

    def test_probe_none_result_leaves_db_alone(self, tmp_path):
        from pikaraoke.lib.events import EventSystem
        from pikaraoke.lib.karaoke_database import KaraokeDatabase
        from pikaraoke.lib.lyrics import LyricsService

        db = KaraokeDatabase(str(tmp_path / "t.db"))
        try:
            song_path = str(tmp_path / "song.mp4")
            open(song_path, "w").close()
            (tmp_path / "song.m4a").write_bytes(b"\x00" * 1024)
            db.insert_songs([{"file_path": song_path, "youtube_id": None, "format": "mp4"}])
            svc = LyricsService(str(tmp_path), EventSystem(), db=db)
            with (
                patch(
                    "pikaraoke.lib.lyrics._classify_language",
                    return_value=([], None),
                ),
                patch(
                    "pikaraoke.lib.lyrics._probe_audio_language",
                    return_value=None,
                ),
            ):
                svc._run_language_classifier(song_path, None)
            song_id = db.get_song_id_by_path(song_path)
            row = db.get_song_by_id(song_id)
            assert row["language"] is None
        finally:
            db.close()
