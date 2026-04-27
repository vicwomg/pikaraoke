"""Unit tests for pikaraoke.lib.vad_probe.

The module wraps two probes (silero VAD + ffmpeg silencedetect) and
merges their outputs into the ``[(onset, next_onset), ...]`` shape the
DP solver in ``lyrics_align`` consumes. Tests stub both probes at the
internal helper boundary so we don't run torch or ffmpeg in CI.
"""

from unittest.mock import patch

import pytest

from pikaraoke.lib import vad_probe


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts with an unloaded model so mocked load paths don't leak."""
    saved_model = vad_probe._model
    saved_unavailable = vad_probe._model_unavailable
    vad_probe._model = None
    vad_probe._model_unavailable = False
    yield
    vad_probe._model = saved_model
    vad_probe._model_unavailable = saved_unavailable


class TestVADProbe:
    def test_returns_sorted_monotonic_pairs(self, monkeypatch):
        # silero finds three phrases; silencedetect finds none new.
        monkeypatch.setattr(
            vad_probe,
            "_silero_onset_starts",
            lambda _p: ([10.0, 20.0, 30.0], 60.0),
        )
        monkeypatch.setattr(vad_probe, "_silencedetect_onset_pairs", lambda _p: [])
        out = vad_probe.list_vocal_onsets("/tmp/song.mp3")
        # Each entry's next_onset is the following onset; final entry's
        # next_onset is the audio duration.
        assert out == [(10.0, 20.0), (20.0, 30.0), (30.0, 60.0)]
        # Strictly monotonic onsets.
        for a, b in zip(out, out[1:]):
            assert b[0] > a[0]

    def test_collapses_adjacent_speech_segments_within_threshold(self, monkeypatch):
        # silero's per-phrase 10.0 and silencedetect's silence_end at 10.3
        # describe the same phrase - merged dedup collapses them.
        monkeypatch.setattr(
            vad_probe,
            "_silero_onset_starts",
            lambda _p: ([10.0, 30.0], 60.0),
        )
        # silencedetect adds a 10.3 onset (sustain 5s) and a real 50.0.
        monkeypatch.setattr(
            vad_probe,
            "_silencedetect_onset_pairs",
            lambda _p: [(10.3, 15.3), (50.0, 60.0)],
        )
        out = vad_probe.list_vocal_onsets("/tmp/song.mp3")
        starts = [o for o, _ in out]
        # 10.0 and 10.3 collapse; 30.0 stays; 50.0 added from silencedetect.
        assert starts == [10.0, 30.0, 50.0]

    def test_filters_silencedetect_microspike(self, monkeypatch):
        # silencedetect can emit silence_end[i] just before silence_start[i+1]
        # ("0.001s of audio between two silences"). The candidate-filter
        # would treat that as a long-sustain anchor because the next merged
        # onset sits seconds away. Drop these at the source.
        monkeypatch.setattr(
            vad_probe, "_silero_onset_starts", lambda _p: ([], 60.0)
        )
        monkeypatch.setattr(
            vad_probe,
            "_silencedetect_onset_pairs",
            # First pair's audio sustain is 1ms - synthetic noise spike.
            lambda _p: [(20.0, 20.001), (40.0, 50.0)],
        )
        out = vad_probe.list_vocal_onsets("/tmp/song.mp3")
        starts = [o for o, _ in out]
        assert 20.0 not in starts
        assert 40.0 in starts

    def test_falls_back_to_silencedetect_when_silero_import_fails(self, monkeypatch):
        # _ensure_model returns None (silero not installed). list_vocal_onsets
        # still returns silencedetect-derived anchors with the silencedetect
        # last audio_end as the duration upper bound.
        monkeypatch.setattr(vad_probe, "_ensure_model", lambda: None)
        monkeypatch.setattr(
            vad_probe,
            "_silero_onset_starts",
            lambda _p: ([], None),
        )
        monkeypatch.setattr(
            vad_probe,
            "_silencedetect_onset_pairs",
            lambda _p: [(5.0, 15.0), (25.0, 40.0)],
        )
        out = vad_probe.list_vocal_onsets("/tmp/song.mp3")
        assert out == [(5.0, 25.0), (25.0, 40.0)]

    def test_uses_module_level_model_singleton(self, monkeypatch):
        # _ensure_model loads silero at most once per process. A second
        # call returns the cached model without re-importing.
        load_calls = {"n": 0}

        class _StubModel:
            pass

            def reset_states(self):
                pass

        def _fake_load_silero_vad():
            load_calls["n"] += 1
            return _StubModel()

        # Patch silero_vad import inside _ensure_model.
        import sys
        from types import SimpleNamespace

        fake_silero = SimpleNamespace(load_silero_vad=_fake_load_silero_vad)
        monkeypatch.setitem(sys.modules, "silero_vad", fake_silero)
        first = vad_probe._ensure_model()
        second = vad_probe._ensure_model()
        assert first is not None
        assert first is second
        assert load_calls["n"] == 1

    def test_ensure_model_idempotent(self, monkeypatch):
        # Belt-and-braces: explicit prewarm-then-prewarm doesn't re-load.
        load_calls = {"n": 0}

        class _StubModel:
            def reset_states(self):
                pass

        def _fake_load_silero_vad():
            load_calls["n"] += 1
            return _StubModel()

        import sys
        from types import SimpleNamespace

        monkeypatch.setitem(
            sys.modules, "silero_vad", SimpleNamespace(load_silero_vad=_fake_load_silero_vad)
        )
        for _ in range(5):
            vad_probe._ensure_model()
        assert load_calls["n"] == 1
