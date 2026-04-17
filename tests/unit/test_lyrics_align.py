"""Unit tests for pikaraoke.lib.lyrics_align."""

import sys
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.lyrics import Word
from pikaraoke.lib.lyrics_align import (
    _interpolate_gaps,
    _normalize,
    map_whisper_to_reference,
)


class TestNormalize:
    def test_lowercases_and_strips_punct(self):
        assert _normalize("Hello!") == "hello"
        assert _normalize("it's") == "its"
        assert _normalize("'quoted'") == "quoted"


class TestMapWhisperToReference:
    def test_perfect_match_transfers_timings(self):
        ref = "hello world"
        whisper = [
            Word("hello", 0.0, 0.5),
            Word("world", 0.5, 1.0),
        ]
        result = map_whisper_to_reference(whisper, ref)
        assert result == [
            Word("hello", 0.0, 0.5),
            Word("world", 0.5, 1.0),
        ]

    def test_case_insensitive_and_punctuation_insensitive(self):
        ref = "Hello, world!"
        whisper = [
            Word("hello", 0.0, 0.5),
            Word("world", 0.5, 1.0),
        ]
        result = map_whisper_to_reference(whisper, ref)
        assert [w.text for w in result] == ["Hello,", "world!"]
        assert result[0].start == 0.0
        assert result[1].end == 1.0

    def test_unmatched_middle_interpolates(self):
        # whisper misses middle word
        ref = "hello amazing world"
        whisper = [
            Word("hello", 0.0, 0.5),
            Word("world", 2.0, 2.5),
        ]
        result = map_whisper_to_reference(whisper, ref)
        assert len(result) == 3
        assert result[0].text == "hello"
        assert result[1].text == "amazing"
        # "amazing" should be interpolated between 0.5 and 2.0
        assert 0.5 <= result[1].start < result[1].end <= 2.0
        assert result[2].text == "world"

    def test_whisper_misheard_word_still_aligns_known_tokens(self):
        # Whisper heard "hi" instead of "how" - sequence matcher still anchors others
        ref = "hello how are you"
        whisper = [
            Word("hello", 0.0, 0.5),
            Word("hi", 0.5, 0.8),
            Word("are", 0.8, 1.2),
            Word("you", 1.2, 1.5),
        ]
        result = map_whisper_to_reference(whisper, ref)
        texts = [w.text for w in result]
        assert texts == ["hello", "how", "are", "you"]
        # hello, are, you are anchored directly
        assert result[0].start == 0.0
        assert result[2].start == 0.8
        assert result[3].start == 1.2

    def test_trailing_unmatched_is_dropped(self):
        ref = "hello world extra trailing"
        whisper = [Word("hello", 0.0, 0.5), Word("world", 0.5, 1.0)]
        result = map_whisper_to_reference(whisper, ref)
        # Only matched prefix is kept
        assert [w.text for w in result] == ["hello", "world"]

    def test_empty_whisper_returns_empty(self):
        assert map_whisper_to_reference([], "hello world") == []

    def test_empty_reference_returns_empty(self):
        assert map_whisper_to_reference([Word("x", 0, 1)], "") == []


class TestInterpolateGaps:
    def test_no_gaps(self):
        matched = [Word("a", 0.0, 0.5), Word("b", 0.5, 1.0)]
        assert _interpolate_gaps(["a", "b"], matched) == matched

    def test_single_gap(self):
        matched = [Word("a", 0.0, 1.0), None, Word("c", 3.0, 4.0)]
        result = _interpolate_gaps(["a", "b", "c"], matched)
        assert len(result) == 3
        assert result[1].text == "b"
        assert result[1].start == pytest.approx(1.0)
        assert result[1].end == pytest.approx(3.0)

    def test_multiple_gap_distributes_evenly(self):
        matched = [Word("a", 0.0, 0.0), None, None, Word("d", 4.0, 4.0)]
        result = _interpolate_gaps(["a", "b", "c", "d"], matched)
        # Gap duration = 4.0 / 2 words = 2.0 each
        assert result[1].start == pytest.approx(0.0)
        assert result[1].end == pytest.approx(2.0)
        assert result[2].start == pytest.approx(2.0)
        assert result[2].end == pytest.approx(4.0)


# ----- WhisperXAligner (with mocked whisperx) -----


@pytest.fixture
def fake_whisperx(monkeypatch):
    """Install a fake whisperx module before WhisperXAligner is imported."""
    fake = MagicMock()
    asr_model = MagicMock()
    asr_model.transcribe.return_value = {
        "language": "en",
        "segments": [],
    }
    fake.load_model.return_value = asr_model
    fake.load_align_model.return_value = (MagicMock(), {"meta": 1})
    fake.align.return_value = {
        "word_segments": [
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
    }
    monkeypatch.setitem(sys.modules, "whisperx", fake)
    return fake


class TestWhisperXAligner:
    def test_align_returns_words_mapped_to_reference(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(model_size="tiny", device="cpu")
        words = aligner.align("/tmp/song.mp4", "hello world")
        assert [w.text for w in words] == ["hello", "world"]
        assert words[0].start == 0.0
        assert words[1].end == 1.0
        fake_whisperx.load_model.assert_called_once_with("tiny", "cpu", compute_type="int8")

    def test_models_cached_between_calls(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(model_size="tiny", device="cpu")
        aligner.align("/tmp/a.mp4", "hello world")
        aligner.align("/tmp/b.mp4", "hello world")
        # ASR model loaded once, alignment model loaded once (same lang)
        assert fake_whisperx.load_model.call_count == 1
        assert fake_whisperx.load_align_model.call_count == 1

    def test_align_model_reloads_on_language_change(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        asr = fake_whisperx.load_model.return_value
        asr.transcribe.side_effect = [
            {"language": "en", "segments": []},
            {"language": "pl", "segments": []},
        ]
        aligner = WhisperXAligner(model_size="tiny", device="cpu")
        aligner.align("/tmp/a.mp4", "hello world")
        aligner.align("/tmp/b.mp4", "czesc swiecie")
        assert fake_whisperx.load_align_model.call_count == 2

    def test_gpu_uses_float16_compute_type(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(model_size="base", device="cuda")
        aligner.align("/tmp/a.mp4", "hi")
        fake_whisperx.load_model.assert_called_once_with("base", "cuda", compute_type="float16")
