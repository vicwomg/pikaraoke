"""Unit tests for the US-43 Whisper language-ID probes (Tier 2a + 2b)."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pikaraoke.lib.lyrics_audio_probe import (
    _FALLBACK_OFFSET,
    _PRIMARY_OFFSET,
    _PROBE_WINDOW_S,
    _SAMPLE_RATE,
    _TIER2A_PREFIX,
    _TIER2B_PREFIX,
    _cache_key,
    probe_language,
    probe_language_whole_song,
    read_cached_verdict,
)


def _tier2a_key(sha: str) -> str:
    return _cache_key(_TIER2A_PREFIX, sha)


def _tier2b_key(sha: str) -> str:
    return _cache_key(_TIER2B_PREFIX, sha)


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
        cache.set(_tier2a_key("sha123"), json.dumps({"lang": "pl", "conf": 0.9}))
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert lang == "pl" and hit is True

    def test_hit_with_negative_verdict_is_still_a_hit(self, cache):
        """Cached "probed, inconclusive" must short-circuit re-probing."""
        cache.set(_tier2a_key("sha123"), json.dumps({"lang": None, "conf": None}))
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert lang is None and hit is True

    def test_corrupt_json_is_treated_as_miss(self, cache):
        cache.set(_tier2a_key("sha123"), "{not-json")
        lang, hit = read_cached_verdict(cache.get, "sha123")
        assert (lang, hit) == (None, False)


# --- probe_language core paths -----------------------------------------


class TestProbeLanguage:
    def test_cache_hit_skips_whisper_entirely(self, cache):
        cache.set(_tier2a_key("sha123"), json.dumps({"lang": "pl", "conf": 0.9}))
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
        cache.set(_tier2a_key("sha123"), json.dumps({"lang": None, "conf": None}))
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
        cached = json.loads(cache.get(_tier2a_key("abc")))
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
        cached = json.loads(cache.get(_tier2a_key("abc")))
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
        cached = json.loads(cache.get(_tier2a_key("abc")))
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
        cached = json.loads(cache.get(_tier2a_key("empty")))
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


# --- Tier 2b whole-song probe ------------------------------------------


class TestProbeLanguageWholeSong:
    def test_cache_hit_short_circuits_whisper(self, cache):
        cache.set(_tier2b_key("sha"), json.dumps({"lang": "pl", "conf": 0.92}))
        model = MagicMock()
        get_model = MagicMock(return_value=model)
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=get_model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=MagicMock(),
        )
        assert result == "pl"
        get_model.assert_not_called()
        model.detect_language.assert_not_called()

    def test_cache_hit_with_negative_verdict_is_honored(self, cache):
        cache.set(_tier2b_key("sha"), json.dumps({"lang": None, "conf": None}))
        get_model = MagicMock()
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=get_model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=MagicMock(),
        )
        assert result is None
        get_model.assert_not_called()

    def test_high_confidence_whole_song_accept(self, cache, audio):
        model = _model([("pl", 0.93)])
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result == "pl"
        # Whole-song probe passes the entire audio buffer, plus VAD on.
        call = model.detect_language.call_args
        assert call.kwargs["vad_filter"] is True
        np.testing.assert_array_equal(call.kwargs["audio"], audio)
        cached = json.loads(cache.get(_tier2b_key("sha")))
        assert cached == {"lang": "pl", "conf": pytest.approx(0.93)}

    def test_low_confidence_returns_none_and_caches_negative(self, cache, audio):
        """Confidence under 0.3 is treated as "no vocal content detected"."""
        model = _model([("pl", 0.15)])
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result is None
        cached = json.loads(cache.get(_tier2b_key("sha")))
        assert cached == {"lang": None, "conf": None}

    def test_model_unavailable_no_cache_poison(self, cache, audio):
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=lambda: None,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result is None
        assert cache.store == {}

    def test_language_subtag_is_normalised(self, cache, audio):
        model = _model([("zh-TW", 0.9)])
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(audio),
        )
        assert result == "zh"

    def test_empty_audio_caches_negative(self, cache):
        empty = np.zeros(0, dtype=np.float32)
        model = MagicMock()
        result = probe_language_whole_song(
            audio_path="/tmp/vocals.mp3",
            audio_sha256="sha",
            get_model=lambda: model,
            cache_get=cache.get,
            cache_set=cache.set,
            decode_audio_fn=_decode_audio(empty),
        )
        assert result is None
        model.detect_language.assert_not_called()
        cached = json.loads(cache.get(_tier2b_key("sha")))
        assert cached == {"lang": None, "conf": None}


# --- LyricsService Tier 2b wiring --------------------------------------


class TestLyricsServiceTier2bWiring:
    """``_run_tier2b_probe`` glue: agreement bumps provenance; disagreement flips."""

    def _seed(self, tmp_path, *, language=None, language_source=None):
        from pikaraoke.lib.events import EventSystem
        from pikaraoke.lib.karaoke_database import KaraokeDatabase
        from pikaraoke.lib.lyrics import LyricsService

        db = KaraokeDatabase(str(tmp_path / "t.db"))
        song_path = str(tmp_path / "song.mp4")
        open(song_path, "w").close()
        db.insert_songs([{"file_path": song_path, "youtube_id": None, "format": "mp4"}])
        song_id = db.get_song_id_by_path(song_path)
        # audio_sha256 is normally populated by ensure_audio_fingerprint earlier
        # in the pipeline; seed it directly so the probe has a cache key.
        db.update_audio_fingerprint(song_id, 1.0, 1024, "sha256-fixture")
        if language and language_source:
            db.update_track_metadata_with_provenance(
                song_id, language_source, {"language": language}
            )
        svc = LyricsService(str(tmp_path), EventSystem(), db=db)
        return db, song_path, song_id, svc

    def test_agreement_bumps_provenance_no_invalidation(self, tmp_path):
        """Tier 2b returning the same lang as DB bumps to whisper_probe_stems."""
        db, song_path, song_id, svc = self._seed(
            tmp_path, language="pl", language_source="itunes_text"
        )
        try:
            with patch(
                "pikaraoke.lib.lyrics._probe_audio_language_whole_song",
                return_value="pl",
            ):
                flipped = svc._run_tier2b_probe(song_path, song_id, "/tmp/stems/vocals.mp3")
            assert flipped is False
            row = db.get_song_by_id(song_id)
            assert row["language"] == "pl"
            sources = db.get_metadata_sources(song_id)
            assert sources["language"] == "whisper_probe_stems"
        finally:
            db.close()

    def test_disagreement_flips_language_and_invalidates_ass(self, tmp_path):
        """Kolorowy wiatr case: Tier 2a said 'en', stems say 'pl' — 'pl' wins."""
        db, song_path, song_id, svc = self._seed(
            tmp_path, language="en", language_source="whisper_probe_raw"
        )
        try:
            # Pretend we have a cached auto-ass + lyrics_sha + aligner_model
            # so we can observe invalidation.
            from pikaraoke.lib.audio_fingerprint import ASS_AUTO_ROLE

            ass_file = tmp_path / "song.ass"
            ass_file.write_text("[Script Info]\n")
            db.upsert_artifacts(song_id, [{"role": ASS_AUTO_ROLE, "path": str(ass_file)}])
            db.update_processing_config(
                song_id, lyrics_source="lrclib", aligner_model="wav2vec2", lyrics_sha="sha-en"
            )

            with patch(
                "pikaraoke.lib.lyrics._probe_audio_language_whole_song",
                return_value="pl",
            ):
                flipped = svc._run_tier2b_probe(song_path, song_id, "/tmp/stems/vocals.mp3")
            assert flipped is True
            row = db.get_song_by_id(song_id)
            assert row["language"] == "pl"
            # Provenance flipped to stems, not the prior raw probe.
            sources = db.get_metadata_sources(song_id)
            assert sources["language"] == "whisper_probe_stems"
            # .ass + lyrics_sha + aligner_model all cleared so the next
            # _do_fetch_and_convert re-fetches LRC in Polish.
            assert not ass_file.exists()
            assert row["lyrics_sha"] is None
            assert row["aligner_model"] is None
        finally:
            db.close()

    def test_inconclusive_probe_is_noop(self, tmp_path):
        db, song_path, song_id, svc = self._seed(
            tmp_path, language="en", language_source="whisper_probe_raw"
        )
        try:
            with patch(
                "pikaraoke.lib.lyrics._probe_audio_language_whole_song",
                return_value=None,
            ):
                flipped = svc._run_tier2b_probe(song_path, song_id, "/tmp/stems/vocals.mp3")
            assert flipped is False
            row = db.get_song_by_id(song_id)
            assert row["language"] == "en"
            sources = db.get_metadata_sources(song_id)
            assert sources["language"] == "whisper_probe_raw"
        finally:
            db.close()

    def test_manual_language_blocks_flip(self, tmp_path):
        """manual rung (100) is sticky; 2b must not overwrite it."""
        db, song_path, song_id, svc = self._seed(tmp_path, language="en", language_source="manual")
        try:
            with patch(
                "pikaraoke.lib.lyrics._probe_audio_language_whole_song",
                return_value="pl",
            ):
                flipped = svc._run_tier2b_probe(song_path, song_id, "/tmp/stems/vocals.mp3")
            assert flipped is False
            row = db.get_song_by_id(song_id)
            assert row["language"] == "en"
            sources = db.get_metadata_sources(song_id)
            assert sources["language"] == "manual"
        finally:
            db.close()

    def test_missing_audio_sha_skips_probe(self, tmp_path):
        """Without audio_sha256, we can't cache — skip entirely."""
        from pikaraoke.lib.events import EventSystem
        from pikaraoke.lib.karaoke_database import KaraokeDatabase
        from pikaraoke.lib.lyrics import LyricsService

        db = KaraokeDatabase(str(tmp_path / "t.db"))
        try:
            song_path = str(tmp_path / "song.mp4")
            open(song_path, "w").close()
            db.insert_songs([{"file_path": song_path, "youtube_id": None, "format": "mp4"}])
            song_id = db.get_song_id_by_path(song_path)
            svc = LyricsService(str(tmp_path), EventSystem(), db=db)
            with patch("pikaraoke.lib.lyrics._probe_audio_language_whole_song") as mock_probe:
                flipped = svc._run_tier2b_probe(song_path, song_id, "/tmp/stems/vocals.mp3")
            assert flipped is False
            mock_probe.assert_not_called()
        finally:
            db.close()
