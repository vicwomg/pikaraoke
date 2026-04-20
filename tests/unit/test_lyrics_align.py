"""Unit tests for pikaraoke.lib.lyrics_align."""

import sys
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.lyrics import Word
from pikaraoke.lib.lyrics_align import (
    _interpolate_gaps,
    _normalize,
    map_whisper_to_reference,
    map_whisper_to_reference_by_lines,
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


class TestMapWhisperToReferenceByLines:
    def test_repeated_phrase_anchors_within_each_line(self):
        # Both lines say "turn around"; whisper correctly times each instance.
        # Global SequenceMatcher can tie-break the ref "turn around"s to the
        # wrong whisper instances and drag later tokens badly off - per-line
        # matching makes that impossible because each window sees only its
        # own whisper words.
        lrc_lines = [(10.0, 15.0, "turn around now"), (60.0, 65.0, "turn around again")]
        whisper = [
            Word("turn", 10.1, 10.4),
            Word("around", 10.5, 10.9),
            Word("now", 11.0, 11.3),
            Word("turn", 60.2, 60.5),
            Word("around", 60.6, 61.0),
            Word("again", 61.1, 61.4),
        ]
        out = map_whisper_to_reference_by_lines(whisper, lrc_lines)
        texts = [w.text for w in out]
        assert texts == ["turn", "around", "now", "turn", "around", "again"]
        # Line 1 words anchor in the 10s range, line 2 in the 60s range.
        for w in out[:3]:
            assert 10.0 <= w.start <= 12.0
        for w in out[3:]:
            assert 60.0 <= w.start <= 62.0

    def test_drifted_whisper_word_cannot_cross_line_boundary(self):
        # Whisper grossly mis-timed the second "turn": placed it inside the
        # first line's window. Per-line matching sees it only for line 1;
        # line 2 has to fall back to uniform timing across its own window.
        lrc_lines = [(10.0, 15.0, "turn around"), (60.0, 65.0, "turn around")]
        whisper = [
            Word("turn", 10.1, 10.4),
            Word("around", 10.5, 10.9),
            Word("turn", 11.0, 11.3),  # drifted: should have been ~60s
            Word("around", 11.4, 11.7),  # drifted
        ]
        out = map_whisper_to_reference_by_lines(whisper, lrc_lines)
        # Line 1 takes the first two whisper matches (in its window).
        assert out[0].start == pytest.approx(10.1)
        # Line 2 has no whisper anchors in its [58.5, 66.5] window, so
        # uniform fallback spreads its two tokens across [60.0, 65.0].
        assert 60.0 <= out[2].start < out[2].end <= 65.0
        assert 60.0 <= out[3].start < out[3].end <= 65.0

    def test_missing_whisper_word_interpolates_within_line(self):
        # Whisper missed "around"; it should be interpolated between the
        # two line anchors without bleeding timing across lines.
        lrc_lines = [(10.0, 15.0, "turn around now")]
        whisper = [
            Word("turn", 10.5, 10.8),
            Word("now", 13.0, 13.5),
        ]
        out = map_whisper_to_reference_by_lines(whisper, lrc_lines)
        assert [w.text for w in out] == ["turn", "around", "now"]
        assert 10.8 <= out[1].start < out[1].end <= 13.0

    def test_line_with_no_whisper_uses_uniform_fallback(self):
        lrc_lines = [(10.0, 20.0, "alpha beta gamma")]
        out = map_whisper_to_reference_by_lines([], lrc_lines)
        assert [w.text for w in out] == ["alpha", "beta", "gamma"]
        # Uniform split across 10s window.
        assert out[0].start == pytest.approx(10.0)
        assert out[-1].end == pytest.approx(20.0)

    def test_empty_input_returns_empty(self):
        assert map_whisper_to_reference_by_lines([], []) == []


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
    def test_align_returns_words_with_wav2vec2_timings(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        words = aligner.align(
            "/tmp/song.mp4",
            "hello world",
            lrc_lines=[(0.0, 5.0, "hello world")],
            language="en",
        )
        assert [w.text for w in words] == ["hello", "world"]
        assert words[0].start == 0.0
        assert words[1].end == 1.0

    def test_skips_whisper_asr_entirely(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/song.mp4",
            "hello world",
            lrc_lines=[(0.0, 5.0, "hello world")],
            language="en",
        )
        # No whisper transcription model is ever loaded; wav2vec2 is the
        # only model in the pipeline.
        fake_whisperx.load_model.assert_not_called()

    def test_passes_lrc_segments_to_wav2vec2(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/song.mp4",
            "hello world",
            lrc_lines=[(1.0, 3.0, "hello"), (3.0, 6.0, "world")],
            language="en",
        )
        segments_arg = fake_whisperx.align.call_args[0][0]
        assert segments_arg == [
            {"start": 1.0, "end": 3.0, "text": "hello"},
            {"start": 3.0, "end": 6.0, "text": "world"},
        ]

    def test_align_model_cached_between_calls(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align("/tmp/a.mp4", "hello", lrc_lines=[(0.0, 1.0, "hello")], language="en")
        aligner.align("/tmp/b.mp4", "hello", lrc_lines=[(0.0, 1.0, "hello")], language="en")
        assert fake_whisperx.load_align_model.call_count == 1

    def test_align_model_reloads_on_language_change(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align("/tmp/a.mp4", "hello", lrc_lines=[(0.0, 1.0, "hello")], language="en")
        aligner.align("/tmp/b.mp4", "czesc", lrc_lines=[(0.0, 1.0, "czesc")], language="pl")
        assert fake_whisperx.load_align_model.call_count == 2

    def test_missing_language_raises(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        with pytest.raises(ValueError, match="language required"):
            aligner.align("/tmp/a.mp4", "hello", lrc_lines=[(0.0, 1.0, "hello")])

    def test_last_detected_language_mirrors_input(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align("/tmp/a.mp4", "hi", lrc_lines=[(0.0, 1.0, "hi")], language="pl")
        assert aligner.last_detected_language == "pl"

    def test_whole_song_fallback_when_no_lrc_lines(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align("/tmp/a.mp4", "hello world", language="en")
        segments_arg = fake_whisperx.align.call_args[0][0]
        assert len(segments_arg) == 1
        assert segments_arg[0]["start"] == 0.0
        assert segments_arg[0]["text"] == "hello world"

    def test_model_id_is_wav2vec2(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        assert aligner.model_id == "wav2vec2-lrc"
