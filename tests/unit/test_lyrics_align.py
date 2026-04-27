"""Unit tests for pikaraoke.lib.lyrics_align."""

import sys
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.lyrics import Word, WordPart
from pikaraoke.lib.lyrics_align import (
    _detect_global_offset,
    _group_chars_by_word,
    _interpolate_gaps,
    _normalize,
    _parts_for_ref,
    _words_with_char_parts,
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

    def test_ctc_bleed_one_word_eats_window_falls_back_to_uniform(self):
        # Pocahontas pattern: previous line's sustained "świaaat" bleeds
        # into this line's audio window, so wav2vec2 places "Lecz" at
        # +5s with a 5.3s duration. The guard discards all anchors and
        # uses uniform timing instead.
        line_start, line_end = 109.0, 116.0  # 7s line window
        whisper = [
            Word("Lecz", 109.0, 114.3),  # 5.3s - more than half the window
            Word("to", 114.3, 114.5),
            Word("będzie", 114.5, 115.5),
            Word("świat", 115.5, 116.0),
        ]
        out = map_whisper_to_reference_by_lines(
            whisper, [(line_start, line_end, "Lecz to będzie świat")]
        )
        assert [w.text for w in out] == ["Lecz", "to", "będzie", "świat"]
        # Uniform fallback spreads tokens across the window evenly,
        # ~1.75s per word - none consume more than half the window.
        for w in out:
            assert (w.end - w.start) == pytest.approx(1.75, abs=0.01)
        assert out[0].start == pytest.approx(line_start)
        assert out[-1].end == pytest.approx(line_end)

    def test_ctc_bleed_first_anchor_past_midpoint_falls_back_to_uniform(self):
        # Even if no single anchor exceeds the duration threshold, the
        # whole phrase being shifted into the back half of the line
        # window (in a multi-word line) is itself a bleed signature.
        line_start, line_end = 100.0, 110.0  # 10s window, midpoint=105
        whisper = [
            Word("alpha", 106.0, 106.5),  # starts 6s into 10s window
            Word("beta", 107.0, 107.5),
            Word("gamma", 108.0, 108.5),
        ]
        out = map_whisper_to_reference_by_lines(
            whisper, [(line_start, line_end, "alpha beta gamma")]
        )
        assert [w.text for w in out] == ["alpha", "beta", "gamma"]
        # Uniform fallback - alpha now starts at line_start, not at +6s.
        assert out[0].start == pytest.approx(line_start)

    def test_single_word_line_keeps_long_sustain(self):
        # A line with one word can legitimately sustain through the whole
        # window (final held note); the bleed guard must not kick in.
        whisper = [Word("świat", 100.0, 108.0)]  # 8s sustained note
        out = map_whisper_to_reference_by_lines(whisper, [(100.0, 108.0, "świat")])
        assert len(out) == 1
        assert out[0].start == pytest.approx(100.0)
        assert out[0].end == pytest.approx(108.0)


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
    """Install a fake whisperx module before WhisperXAligner is imported.

    The fake returns the post-``return_char_alignments=True`` shape: per
    segment a flat ``chars`` list plus a ``words`` list. Char timings
    are synthetic (0.1s per glyph) but the structure matches what real
    whisperx emits, which is what ``_words_with_char_parts`` consumes.
    """
    fake = MagicMock()
    fake.load_align_model.return_value = (MagicMock(), {"meta": 1})
    fake.align.return_value = {
        "segments": [
            {
                "text": "hello world",
                "start": 0.0,
                "end": 1.0,
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "world", "start": 0.5, "end": 1.0},
                ],
                "chars": [
                    {"char": "h", "start": 0.0, "end": 0.1},
                    {"char": "e", "start": 0.1, "end": 0.2},
                    {"char": "l", "start": 0.2, "end": 0.3},
                    {"char": "l", "start": 0.3, "end": 0.4},
                    {"char": "o", "start": 0.4, "end": 0.5},
                    {"char": " "},
                    {"char": "w", "start": 0.5, "end": 0.6},
                    {"char": "o", "start": 0.6, "end": 0.7},
                    {"char": "r", "start": 0.7, "end": 0.8},
                    {"char": "l", "start": 0.8, "end": 0.9},
                    {"char": "d", "start": 0.9, "end": 1.0},
                ],
            }
        ],
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

    def test_whole_song_segment_clamped_to_audio_duration(self, fake_whisperx, monkeypatch):
        """The no-LRC fallback must bound the segment by actual audio
        length. Otherwise hallucinated reference text can make wav2vec2
        overshoot and produce timestamps hours past the song, which
        crashes libass on createTrack."""
        from pikaraoke.lib import lyrics_align

        monkeypatch.setattr(lyrics_align, "_probe_audio_duration", lambda _p: 210.0)
        aligner = lyrics_align.WhisperXAligner(device="cpu")
        aligner.align("/tmp/a.mp4", "hello world", language="en")
        segments_arg = fake_whisperx.align.call_args[0][0]
        assert segments_arg[0]["end"] == 210.0

    def test_words_past_audio_duration_dropped(self, fake_whisperx, monkeypatch):
        """Safety net: words aligned past audio length are dropped.
        This is the Genius whole-song path — no LRC line windows — so
        wav2vec2 can overshoot when reference text includes junk like
        '4 ContributorsTranslationsEnglish'."""
        from pikaraoke.lib import lyrics_align

        monkeypatch.setattr(lyrics_align, "_probe_audio_duration", lambda _p: 210.0)
        # wav2vec2 hallucinates a word at 9999s.
        fake_whisperx.align.return_value = {
            "segments": [
                {
                    "text": "hello world",
                    "start": 0.0,
                    "end": 9999.0,
                    "words": [
                        {"word": "hello", "start": 1.0, "end": 2.0},
                        {"word": "world", "start": 9998.0, "end": 9999.0},
                    ],
                    "chars": [
                        {"char": "h", "start": 1.0, "end": 1.2},
                        {"char": "e", "start": 1.2, "end": 1.4},
                        {"char": "l", "start": 1.4, "end": 1.6},
                        {"char": "l", "start": 1.6, "end": 1.8},
                        {"char": "o", "start": 1.8, "end": 2.0},
                        {"char": " "},
                        {"char": "w", "start": 9998.0, "end": 9998.2},
                        {"char": "o", "start": 9998.2, "end": 9998.4},
                        {"char": "r", "start": 9998.4, "end": 9998.6},
                        {"char": "l", "start": 9998.6, "end": 9998.8},
                        {"char": "d", "start": 9998.8, "end": 9999.0},
                    ],
                }
            ],
        }
        aligner = lyrics_align.WhisperXAligner(device="cpu")
        words = aligner.align("/tmp/a.mp4", "hello world", language="en")
        assert [w.text for w in words] == ["hello"]

    def test_model_id_is_wav2vec2(self, fake_whisperx):
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        assert aligner.model_id == "wav2vec2-char-globaloffset"

    def test_no_offset_detected_leaves_state_zero(self, fake_whisperx):
        # Aligned timings line up with LRC - no offset, no re-align.
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        aligner = WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/song.mp4",
            "hello world",
            lrc_lines=[(0.0, 5.0, "hello world")],
            language="en",
        )
        assert aligner.last_global_offset_s == 0.0
        # Single wav2vec2 call; no shifted-segment re-run.
        assert fake_whisperx.align.call_count == 1

    def test_global_offset_triggers_realignment(self, fake_whisperx):
        # Six LRC lines all reporting their first sung word ~1.8s late
        # (typical YouTube intro-padding mismatch). Aligner should detect
        # the offset, log it, and re-align with shifted segments.
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        lrc_lines = [(t, t + 3.0, "hello") for t in (10.0, 14.0, 18.0, 22.0, 26.0, 30.0)]
        first_starts = [11.85, 15.80, 19.78, 23.82, 27.79, 31.83]
        fake_whisperx.align.return_value = {
            "segments": [
                {
                    "text": "hello",
                    "start": float(s),
                    "end": float(s) + 0.5,
                    "words": [{"word": "hello", "start": s, "end": s + 0.5}],
                    "chars": [
                        {"char": "h", "start": s, "end": s + 0.1},
                        {"char": "e", "start": s + 0.1, "end": s + 0.2},
                        {"char": "l", "start": s + 0.2, "end": s + 0.3},
                        {"char": "l", "start": s + 0.3, "end": s + 0.4},
                        {"char": "o", "start": s + 0.4, "end": s + 0.5},
                    ],
                }
                for s in first_starts
            ]
        }
        aligner = WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/song.mp4",
            "hello hello hello hello hello hello",
            lrc_lines=lrc_lines,
            language="en",
        )
        assert aligner.last_global_offset_s == pytest.approx(1.83, abs=0.05)
        # Two wav2vec2 calls: original + shifted re-run.
        assert fake_whisperx.align.call_count == 2
        # Second call's segments are shifted by the detected offset.
        second_segments = fake_whisperx.align.call_args_list[1][0][0]
        first_segments = fake_whisperx.align.call_args_list[0][0][0]
        for orig, shifted in zip(first_segments, second_segments):
            assert shifted["start"] == pytest.approx(orig["start"] + aligner.last_global_offset_s)
            assert shifted["text"] == orig["text"]

    def test_offset_state_resets_per_call(self, fake_whisperx):
        # First song detects an offset; the next song with consistent
        # alignment must not inherit the previous song's offset.
        from pikaraoke.lib.lyrics_align import WhisperXAligner

        lrc_lines_offset = [(t, t + 3.0, "hello") for t in (10.0, 14.0, 18.0, 22.0, 26.0, 30.0)]
        first_starts = [11.85, 15.80, 19.78, 23.82, 27.79, 31.83]
        fake_whisperx.align.return_value = {
            "segments": [
                {
                    "text": "hello",
                    "start": float(s),
                    "end": float(s) + 0.5,
                    "words": [{"word": "hello", "start": s, "end": s + 0.5}],
                    "chars": [{"char": "h", "start": s, "end": s + 0.1}],
                }
                for s in first_starts
            ]
        }
        aligner = WhisperXAligner(device="cpu")
        aligner.align("/tmp/a.mp4", "hello", lrc_lines=lrc_lines_offset, language="en")
        assert aligner.last_global_offset_s != 0.0

        # Next song: clean alignment, single segment, no offset signal.
        fake_whisperx.align.return_value = {
            "segments": [
                {
                    "text": "hi",
                    "start": 0.0,
                    "end": 0.5,
                    "words": [{"word": "hi", "start": 0.0, "end": 0.5}],
                    "chars": [{"char": "h", "start": 0.0, "end": 0.5}],
                }
            ]
        }
        aligner.align("/tmp/b.mp4", "hi", lrc_lines=[(0.0, 1.0, "hi")], language="en")
        assert aligner.last_global_offset_s == 0.0


class TestCharAlignmentExtraction:
    def test_group_chars_splits_on_spaces(self):
        chars = [
            {"char": "h", "start": 0.0, "end": 0.1},
            {"char": "i", "start": 0.1, "end": 0.2},
            {"char": " "},
            {"char": "y", "start": 0.3, "end": 0.4},
            {"char": "o", "start": 0.4, "end": 0.5},
        ]
        groups = _group_chars_by_word(chars)
        assert len(groups) == 2
        assert [e["char"] for e in groups[0]] == ["h", "i"]
        assert [e["char"] for e in groups[1]] == ["y", "o"]

    def test_group_chars_collapses_leading_space(self):
        chars = [{"char": " "}, {"char": "a", "start": 0.0, "end": 0.1}]
        groups = _group_chars_by_word(chars)
        assert len(groups) == 1
        assert [e["char"] for e in groups[0]] == ["a"]

    def test_words_with_char_parts_produces_per_glyph_parts(self):
        aligned = {
            "segments": [
                {
                    "text": "ab",
                    "start": 0.0,
                    "end": 0.2,
                    "words": [{"word": "ab", "start": 0.0, "end": 0.2}],
                    "chars": [
                        {"char": "a", "start": 0.0, "end": 0.1},
                        {"char": "b", "start": 0.1, "end": 0.2},
                    ],
                }
            ]
        }
        words = _words_with_char_parts(aligned)
        assert len(words) == 1
        assert words[0].parts is not None
        assert [p.text for p in words[0].parts] == ["a", "b"]
        assert words[0].parts[0].start == 0.0
        assert words[0].parts[1].end == 0.2

    def test_words_with_char_parts_skips_untimed_chars(self):
        # A char that wav2vec2 couldn't align (no start/end) drops out of
        # parts; the remaining glyphs still form valid parts.
        aligned = {
            "segments": [
                {
                    "text": "abc",
                    "start": 0.0,
                    "end": 0.3,
                    "words": [{"word": "abc", "start": 0.0, "end": 0.3}],
                    "chars": [
                        {"char": "a", "start": 0.0, "end": 0.1},
                        {"char": "b"},  # untimed
                        {"char": "c", "start": 0.2, "end": 0.3},
                    ],
                }
            ]
        }
        words = _words_with_char_parts(aligned)
        assert [p.text for p in words[0].parts] == ["a", "c"]

    def test_words_with_char_parts_none_when_single_part(self):
        # Word with only one aligned glyph renders as a single \kf - no
        # point attaching a single-element parts tuple.
        aligned = {
            "segments": [
                {
                    "text": "ab",
                    "start": 0.0,
                    "end": 0.2,
                    "words": [{"word": "ab", "start": 0.0, "end": 0.2}],
                    "chars": [
                        {"char": "a", "start": 0.0, "end": 0.1},
                        {"char": "b"},  # untimed
                    ],
                }
            ]
        }
        words = _words_with_char_parts(aligned)
        assert words[0].parts is None

    def test_smooths_ctc_spike_into_uniform_distribution(self):
        # Pocahontas "stworzeń" pattern: CTC dumps 1.36s on 'o' and packs
        # the trailing chars into 20ms each. Smoothing replaces with an
        # even spread across the word's span - same total duration.
        aligned = {
            "segments": [
                {
                    "text": "stworzen",
                    "start": 0.0,
                    "end": 3.06,
                    "words": [{"word": "stworzen", "start": 0.0, "end": 3.06}],
                    "chars": [
                        {"char": "s", "start": 0.0, "end": 0.52},
                        {"char": "t", "start": 0.52, "end": 0.70},
                        {"char": "w", "start": 0.70, "end": 0.76},
                        {"char": "o", "start": 0.76, "end": 0.96},
                        {"char": "r", "start": 0.96, "end": 2.32},  # spike: 1.36s
                        {"char": "z", "start": 2.32, "end": 3.02},
                        {"char": "e", "start": 3.02, "end": 3.04},
                        {"char": "n", "start": 3.04, "end": 3.06},
                    ],
                }
            ]
        }
        words = _words_with_char_parts(aligned)
        parts = words[0].parts
        assert parts is not None
        assert [p.text for p in parts] == ["s", "t", "w", "o", "r", "z", "e", "n"]
        # 8 chars across 3.06s = 0.3825s each, all uniform.
        per = 3.06 / 8
        for i, p in enumerate(parts):
            assert p.start == pytest.approx(per * i, abs=1e-6)
            assert (p.end - p.start) == pytest.approx(per, abs=1e-6)

    def test_does_not_smooth_balanced_char_durations(self):
        # Normal aligned word with even per-char timings: smoothing must
        # not touch it (no CTC spike to fix).
        aligned = {
            "segments": [
                {
                    "text": "hello",
                    "start": 0.0,
                    "end": 0.5,
                    "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
                    "chars": [
                        {"char": "h", "start": 0.00, "end": 0.10},
                        {"char": "e", "start": 0.10, "end": 0.20},
                        {"char": "l", "start": 0.20, "end": 0.30},
                        {"char": "l", "start": 0.30, "end": 0.40},
                        {"char": "o", "start": 0.40, "end": 0.50},
                    ],
                }
            ]
        }
        parts = _words_with_char_parts(aligned)[0].parts
        # Original timings preserved exactly.
        assert parts[0].start == 0.00
        assert parts[0].end == 0.10
        assert parts[4].start == 0.40
        assert parts[4].end == 0.50


class TestDetectGlobalOffset:
    @staticmethod
    def _aligned(first_starts: list[float]) -> dict:
        # Minimal whisperx-shaped result: one segment per first_start, each
        # carrying a single timed word. Detector only reads ``words[0].start``.
        return {
            "segments": [
                {"words": [{"word": "x", "start": s, "end": s + 0.3}]} for s in first_starts
            ]
        }

    def test_returns_median_when_offset_is_consistent(self):
        # 6 lines with first-word anchors all roughly +1.8s past the LRC
        # line start - the YouTube-vs-Spotify intro-padding pattern.
        lrc_lines = [(t, t + 3.0, "x") for t in (10.0, 14.0, 18.0, 22.0, 26.0, 30.0)]
        aligned = self._aligned([11.85, 15.80, 19.78, 23.82, 27.79, 31.83])
        offset = _detect_global_offset(aligned, lrc_lines)
        assert offset is not None
        assert offset == pytest.approx(1.83, abs=0.05)

    def test_returns_none_when_too_few_lines(self):
        lrc_lines = [(0.0, 1.0, "x"), (1.0, 2.0, "x")]
        aligned = self._aligned([2.0, 3.0])
        assert _detect_global_offset(aligned, lrc_lines) is None

    def test_returns_none_when_median_below_threshold(self):
        # All deltas under 0.5s - natural drift, not a global offset worth
        # correcting (bleed-guard handles small per-line drift on its own).
        lrc_lines = [(t, t + 2.0, "x") for t in (0.0, 2.0, 4.0, 6.0, 8.0)]
        aligned = self._aligned([0.1, 2.2, 4.15, 6.05, 8.2])
        assert _detect_global_offset(aligned, lrc_lines) is None

    def test_returns_none_when_iqr_too_wide(self):
        # Median is +2s but deltas spread from -1 to +5 - that's tempo
        # drift or wav2vec2 noise, not a constant intro offset.
        lrc_lines = [(t, t + 2.0, "x") for t in (0.0, 5.0, 10.0, 15.0, 20.0)]
        aligned = self._aligned([-1.0, 7.0, 12.0, 14.0, 25.0])
        assert _detect_global_offset(aligned, lrc_lines) is None

    def test_skips_segments_without_timed_words(self):
        # Line 3 has no timed first word - skip it, still compute median
        # from the remaining 5 lines.
        lrc_lines = [(t, t + 3.0, "x") for t in (10.0, 14.0, 18.0, 22.0, 26.0, 30.0)]
        aligned = {
            "segments": [
                {"words": [{"word": "x", "start": 11.8, "end": 12.1}]},
                {"words": [{"word": "x", "start": 15.8, "end": 16.1}]},
                {"words": [{"word": "x"}]},  # no start - segment dropped
                {"words": [{"word": "x", "start": 23.8, "end": 24.1}]},
                {"words": [{"word": "x", "start": 27.8, "end": 28.1}]},
                {"words": [{"word": "x", "start": 31.8, "end": 32.1}]},
            ]
        }
        assert _detect_global_offset(aligned, lrc_lines) == pytest.approx(1.8, abs=0.05)

    def test_zip_skips_empty_lrc_lines(self):
        # ``_build_segments`` filters empty-text lines, so segments are
        # 1:1 with the non-empty subset - the detector must skip the same
        # entries to keep the alignment honest.
        lrc_lines = [
            (10.0, 12.0, "x"),
            (12.0, 14.0, ""),  # filtered out upstream
            (14.0, 16.0, "x"),
            (16.0, 18.0, "x"),
            (18.0, 20.0, "x"),
            (20.0, 22.0, "x"),
        ]
        aligned = self._aligned([11.8, 15.8, 17.8, 19.8, 21.8])
        offset = _detect_global_offset(aligned, lrc_lines)
        assert offset == pytest.approx(1.8, abs=0.05)


class TestPartsForRef:
    def test_verbatim_match_returns_parts_unchanged(self):
        parts = (WordPart("a", 0.0, 0.1), WordPart("b", 0.1, 0.2))
        assert _parts_for_ref(parts, "ab") is parts

    def test_trailing_punctuation_appended_to_last_part(self):
        parts = (WordPart("h", 0.0, 0.1), WordPart("i", 0.1, 0.2))
        out = _parts_for_ref(parts, "hi!")
        assert out is not None
        assert [p.text for p in out] == ["h", "i!"]
        # Timings of the appended punct match the last part - we had no
        # separate timing for the ',' glyph in the aligned output.
        assert out[-1].end == 0.2

    def test_leading_punctuation_prefixed_to_first_part(self):
        parts = (WordPart("h", 0.1, 0.2),)
        out = _parts_for_ref(parts, '"h')
        assert [p.text for p in out] == ['"h']

    def test_irreconcilable_returns_none(self):
        parts = (WordPart("x", 0.0, 0.1),)
        assert _parts_for_ref(parts, "totally-different") is None

    def test_none_input_returns_none(self):
        assert _parts_for_ref(None, "abc") is None
