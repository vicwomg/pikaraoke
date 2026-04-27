"""Unit tests for pikaraoke.lib.lyrics_align."""

import sys
from unittest.mock import MagicMock

import pytest

from pikaraoke.lib.lyrics import Word, WordPart
from pikaraoke.lib.lyrics_align import (
    _align_lines_to_anchors_dp,
    _detect_per_line_starts,
    _group_chars_by_word,
    _interpolate_gaps,
    _interpolate_unanchored,
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
        assert aligner.model_id == "wav2vec2-char-vad-dpalign"

    def test_no_shift_when_no_leading_silence(self, fake_whisperx, monkeypatch):
        # vad_probe returns nothing - no anchors to lock against, so
        # the aligner runs without modification.
        from pikaraoke.lib import lyrics_align, vad_probe

        monkeypatch.setattr(vad_probe, "list_vocal_onsets", lambda _p: [])
        aligner = lyrics_align.WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/song.mp4",
            "hello world",
            lrc_lines=[(0.0, 5.0, "hello world")],
            language="en",
        )
        assert aligner.last_line_starts == {}
        assert fake_whisperx.align.call_count == 1
        segments_arg = fake_whisperx.align.call_args[0][0]
        assert segments_arg[0]["start"] == 0.0

    def test_per_line_shift_drives_segments_and_mapping(self, fake_whisperx, monkeypatch):
        # YouTube rip with 1.83s extra intro padding vs LRCLib's source:
        # vocals start at 16.11s but LRC says 14.28s. wav2vec2 receives
        # already-shifted segments so its forced alignment runs against
        # audio it can actually anchor to. The shift includes a 0.25s
        # karaoke lead-in so segments arrive just before the vocal peak.
        from pikaraoke.lib import lyrics_align, vad_probe

        monkeypatch.setattr(vad_probe, "list_vocal_onsets", lambda _p: [(16.11, 76.11)])
        aligner = lyrics_align.WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/song.mp4",
            "hello world",
            lrc_lines=[(14.28, 17.27, "hello world")],
            language="en",
        )
        expected_start = 16.11 - 0.25
        assert 14.28 in aligner.last_line_starts
        assert aligner.last_line_starts[14.28] == pytest.approx(expected_start, abs=0.01)
        # Single wav2vec2 call - shift happens before alignment runs.
        assert fake_whisperx.align.call_count == 1
        segments_arg = fake_whisperx.align.call_args[0][0]
        assert segments_arg[0]["start"] == pytest.approx(expected_start, abs=0.01)

    def test_shift_state_resets_per_call(self, fake_whisperx, monkeypatch):
        # First song detects per-line shifts; the next song with no
        # leading silence must not inherit the previous song's mapping.
        from pikaraoke.lib import lyrics_align, vad_probe

        onsets_per_path: dict[str, list[tuple[float, float]]] = {
            "/tmp/a.mp4": [(16.11, 76.11)],
            "/tmp/b.mp4": [],
        }
        monkeypatch.setattr(
            vad_probe,
            "list_vocal_onsets",
            lambda p: onsets_per_path[p],
        )
        aligner = lyrics_align.WhisperXAligner(device="cpu")
        aligner.align(
            "/tmp/a.mp4",
            "hello",
            lrc_lines=[(14.28, 17.27, "hello")],
            language="en",
        )
        assert aligner.last_line_starts != {}
        aligner.align("/tmp/b.mp4", "hi", lrc_lines=[(0.0, 1.0, "hi")], language="en")
        assert aligner.last_line_starts == {}


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


class TestDetectPerLineStarts:
    @staticmethod
    def _patch_silences(monkeypatch, ends: list[float]) -> None:
        """Mock vad_probe to emit one onset per silence-end time.

        Each onset's ``next_onset`` is the following onset (so sustain
        looks like the time-to-next-anchor). The final entry's
        ``next_onset`` is +60 s past the last so it sustains long
        enough that the candidate filter never drops it.
        """
        from pikaraoke.lib import vad_probe

        def _fake(_path: str) -> list[tuple[float, float]]:
            sorted_ends = sorted(ends)
            pairs: list[tuple[float, float]] = []
            for i, t in enumerate(sorted_ends):
                next_t = sorted_ends[i + 1] if i + 1 < len(sorted_ends) else t + 60.0
                pairs.append((float(t), float(next_t)))
            return pairs

        monkeypatch.setattr(vad_probe, "list_vocal_onsets", _fake)

    def test_uniform_shift_when_all_lines_have_silence_anchors(self, monkeypatch):
        # Every line preceded by a silence boundary that's +1.83s past
        # its LRC time - silence-based anchoring locks onto each one and
        # the result is a clean uniform shift minus the lead-in.
        self._patch_silences(monkeypatch, [16.11, 19.10, 22.79, 26.15])
        lrc_lines = [
            (14.28, 17.27, "a"),
            (17.27, 20.96, "b"),
            (20.96, 24.32, "c"),
            (24.32, 28.37, "d"),
        ]
        out = _detect_per_line_starts("/tmp/vocals.mp3", lrc_lines)
        assert out is not None
        # Each new_start = silence_end - lead_in (0.25s).
        assert out[0] == pytest.approx(16.11 - 0.25, abs=0.01)
        assert out[1] == pytest.approx(19.10 - 0.25, abs=0.01)
        assert out[2] == pytest.approx(22.79 - 0.25, abs=0.01)
        assert out[3] == pytest.approx(26.15 - 0.25, abs=0.01)

    def test_per_verse_drift_locks_each_locked_line_independently(self, monkeypatch):
        # Mam Tę Moc pattern: line 1 drifts +1.72s, line 6 drifts +3.17s
        # because the YouTube version slows down across verses. A single
        # global shift would over-correct line 1 or under-correct line 6;
        # silence-end matching catches each verse's true onset.
        self._patch_silences(monkeypatch, [16.00, 22.68, 27.86, 30.42, 37.71])
        lrc_lines = [
            (14.28, 17.27, "verse1a"),
            (17.27, 20.96, "verse1b"),  # continuous with line 0, no anchor
            (20.96, 24.32, "verse2"),
            (24.32, 28.37, "verse3"),
            (28.37, 34.54, "verse4"),
            (34.54, 42.55, "verse5"),
        ]
        out = _detect_per_line_starts("/tmp/vocals.mp3", lrc_lines)
        assert out is not None
        # Anchored lines snap to silence_end - lead_in.
        assert out[0] == pytest.approx(16.00 - 0.25, abs=0.01)
        assert out[2] == pytest.approx(22.68 - 0.25, abs=0.01)
        assert out[3] == pytest.approx(27.86 - 0.25, abs=0.01)
        assert out[4] == pytest.approx(30.42 - 0.25, abs=0.01)
        assert out[5] == pytest.approx(37.71 - 0.25, abs=0.01)
        # Continuous line inherits the previous lock's cumulative shift
        # (+1.72 from line 0) - no audio anchor, but it tracks the verse.
        assert out[1] == pytest.approx(17.27 + 1.72 - 0.25, abs=0.05)

    def test_continuous_line_inherits_running_offset(self, monkeypatch):
        # Two LRC lines but only one silence anchor (the song starts
        # singing line 1, line 2 is continuous). Line 2 inherits line 1's
        # shift rather than mis-snapping to a distant silence.
        self._patch_silences(monkeypatch, [16.00, 99.0])
        lrc_lines = [(14.28, 17.27, "a"), (17.27, 20.96, "b")]
        out = _detect_per_line_starts("/tmp/vocals.mp3", lrc_lines)
        assert out is not None
        assert out[0] == pytest.approx(16.00 - 0.25, abs=0.01)
        # Line 1 + cumulative shift (+1.72) - lead_in.
        assert out[1] == pytest.approx(17.27 + 1.72 - 0.25, abs=0.01)

    def test_returns_none_when_vad_yields_nothing(self, monkeypatch):
        # vad_probe returned an empty list - no anchors to lock against,
        # fall back to no shift.
        self._patch_silences(monkeypatch, [])
        assert _detect_per_line_starts("/tmp/v.mp3", [(0.0, 1.0, "x")]) is None

    def test_returns_none_when_initial_offset_below_threshold(self, monkeypatch):
        # 300ms first-line drift sits below the bleed-guard pain point;
        # not worth touching even though silencedetect found an anchor.
        self._patch_silences(monkeypatch, [14.58])
        assert _detect_per_line_starts("/tmp/v.mp3", [(14.28, 17.27, "a")]) is None

    def test_returns_none_when_initial_offset_exceeds_cap(self, monkeypatch):
        # 30s shift would mean the LRC came from a totally different
        # mastering or the silence probe fired on the wrong gap.
        self._patch_silences(monkeypatch, [44.0])
        assert _detect_per_line_starts("/tmp/v.mp3", [(14.28, 17.27, "a")]) is None

    def test_skips_empty_lrc_lines_when_picking_first(self, monkeypatch):
        self._patch_silences(monkeypatch, [16.11])
        lrc_lines = [(10.0, 14.0, ""), (14.28, 17.27, "Na")]
        out = _detect_per_line_starts("/tmp/v.mp3", lrc_lines)
        assert out is not None
        # Empty line still gets a shifted timestamp (cumulative shift
        # applied) - keeps indices aligned with lrc_lines for the
        # caller's mapping.
        assert out[1] == pytest.approx(16.11 - 0.25, abs=0.01)


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


# Helper for the DP / interpolation tests. The DP works with onset
# tuples ``(onset, next_onset)``; in unit tests we typically generate
# pairs from a list of onset times by zipping each with the next.
def _onset_pairs(times: list[float], duration: float = 999.0) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for i, t in enumerate(times):
        next_t = times[i + 1] if i + 1 < len(times) else duration
        out.append((float(t), float(next_t)))
    return out


class TestDPAlignment:
    def test_dp_assigns_one_line_per_anchor_when_lines_match_anchors(self):
        # Three lines line up cleanly with three onsets - DP picks the
        # 1:1 assignment that matches expected times.
        lrc = [
            (10.0, 14.0, "alpha alpha"),
            (14.0, 18.0, "beta beta"),
            (18.0, 22.0, "gamma gamma"),
        ]
        onsets = _onset_pairs([12.0, 16.0, 20.0])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out == [12.0, 16.0, 20.0]

    def test_dp_distributes_cluster_between_flanking_anchors(self):
        # Two anchored lines flank a cluster of unanchored ones (no
        # plausible anchor for the middle two). DP places anchors near
        # the start and end of the LRC span; interpolation handles the
        # cluster. The exact anchored row may shift between two
        # equivalent assignments (cost-tied), but the cluster shape
        # must not collapse to "all skip" or "all anchor".
        lrc = [
            (10.0, 12.0, "anchor1"),
            (12.0, 14.0, "filler1"),
            (14.0, 16.0, "filler2"),
            (16.0, 18.0, "anchor2"),
        ]
        # Only two onsets in the audio: clearly hosting one early and
        # one late line.
        onsets = _onset_pairs([12.0, 18.0])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out is not None
        # Both onsets get used somewhere - DP doesn't waste them.
        anchored = [t for t in out if t is not None]
        assert sorted(anchored) == [12.0, 18.0]
        # The anchors land in the *first half* and *second half* of
        # the LRC line list (i.e. the cluster is correctly split).
        anchor_indices = [i for i, t in enumerate(out) if t is not None]
        assert any(i < 2 for i in anchor_indices), "first anchor must land on an early LRC line"
        assert any(i >= 2 for i in anchor_indices), "second anchor must land on a late LRC line"

    def test_dp_prefers_higher_sustain_for_long_lines(self):
        # A long line with two candidate anchors: one with 0.5s sustain
        # (too short for an 8-word line), one with 5s sustain. The DP's
        # sustain-shortfall term should push the assignment toward the
        # 5s anchor.
        lrc = [(10.0, 18.0, " ".join(["w"] * 8))]
        onsets = [(11.0, 11.5), (12.0, 17.0)]  # short, then long
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out == [12.0]

    def test_dp_prefers_anchor_when_clearly_matching(self):
        # ER2 regression: skip_cost was scaled by num_words to ensure
        # anchoring beats skipping for any line that has a plausible
        # candidate. A 5-word line with a perfectly-matching anchor must
        # anchor, never skip.
        lrc = [(10.0, 14.0, "the quick brown fox jumps")]
        onsets = _onset_pairs([10.5])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out == [10.5]

    def test_dp_falls_through_to_inherit_when_no_anchors_exist(self):
        # No onsets at all - DP can't assign anything; returns all-None.
        # The orchestrator's earlier no-onset gate would normally bail
        # out before reaching the DP, but the DP itself must not crash.
        lrc = [(10.0, 14.0, "a"), (14.0, 18.0, "b")]
        out = _align_lines_to_anchors_dp(lrc, [])
        assert out is None

    def test_dp_returns_none_when_first_anchor_offset_exceeds_cap(self):
        # ER3 regression: the first anchored line's shift must respect
        # _GLOBAL_OFFSET_MAX_S. A 30s drift is a wrong-onset signal, not
        # something we should silently propagate to all later lines.
        lrc = [(10.0, 14.0, "a")]
        onsets = _onset_pairs([45.0])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out is None

    def test_dp_preserves_per_verse_drift_case(self):
        # Mam Tę Moc pattern: each verse drifts a little more than the
        # last. A single global shift would break later verses; the DP
        # anchors per-verse so each one snaps to its own onset.
        lrc = [
            (14.28, 17.27, "verse1"),
            (20.96, 24.32, "verse2"),
            (28.37, 34.54, "verse3"),
            (34.54, 42.55, "verse4"),
        ]
        # +1.72 / +1.72 / -0.51 / +3.17 drift across verses.
        onsets = _onset_pairs([16.00, 22.68, 27.86, 37.71])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out == [16.00, 22.68, 27.86, 37.71]

    def test_dp_handles_first_few_lrc_lines_with_no_preceding_anchor(self):
        # First two lines are far in LRC time from the only available
        # onset; line 2's LRC time matches the onset within the
        # proximity slack so the DP anchors line 2 alone. Leading lines
        # then interpolate backward from line 2 in the orchestrator.
        lrc = [
            (10.0, 12.0, "a"),
            (12.0, 14.0, "b"),
            (50.0, 52.0, "c"),
        ]
        # Single onset matches line 2 (within slack); too far for 0/1.
        onsets = _onset_pairs([50.5])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out[0] is None
        assert out[1] is None
        assert out[2] == 50.5

    def test_dp_skips_short_filler_line_without_anchor(self):
        # A filler line with no plausible anchor should be skipped; the
        # DP shouldn't force it onto a far-away anchor.
        lrc = [
            (10.0, 12.0, "main line one"),
            (12.0, 12.5, "x"),  # filler
            (12.5, 15.0, "main line three"),
        ]
        onsets = _onset_pairs([10.5, 13.0])
        out = _align_lines_to_anchors_dp(lrc, onsets)
        assert out[0] == 10.5
        assert out[2] == 13.0
        assert out[1] is None


class TestInterpolation:
    def test_interpolate_unanchored_proportional_to_lrc_durations(self):
        # Cluster of unanchored lines between two anchors. The unanchored
        # lines' LRC durations are 1s and 3s; the available audio window
        # is 10s. Distribution puts the first line ~1/4 of the way through
        # and the second ~3/4.
        lrc = [
            (0.0, 2.0, "anchored1"),
            (2.0, 3.0, "filler1"),
            (3.0, 6.0, "filler2"),
            (6.0, 10.0, "anchored2"),
        ]
        assignment: list[float | None] = [10.0, None, None, 20.0]
        out = _interpolate_unanchored(assignment, lrc)
        assert out[0] == 10.0
        assert out[3] == 20.0
        # filler1 at LRC 2.0 sits 33% through the LRC span 0..6 -> 13.33
        # filler2 at LRC 3.0 sits 50% through -> 15.0
        assert out[1] == pytest.approx(13.333, abs=0.01)
        assert out[2] == pytest.approx(15.0, abs=0.01)

    def test_interpolate_clamps_pre_anchor_lines_at_zero(self):
        # ER3 regression: pre-anchor lines extrapolate backward from the
        # first anchor. If the original LRC pushed a line "before time 0"
        # (e.g. very short LRC intro), clamping must keep all outputs >= 0.
        lrc = [
            (0.5, 1.0, "very early"),
            (1.0, 2.0, "early"),
            (5.0, 6.0, "first anchored"),
        ]
        assignment: list[float | None] = [None, None, 2.0]
        out = _interpolate_unanchored(assignment, lrc)
        # Line 0 extrapolates to 2.0 - (5.0 - 0.5) = -2.5 -> clamped to 0.
        # Line 1 extrapolates to 2.0 - (5.0 - 1.0) = -2.0 -> clamped to 0.
        assert out[0] == 0.0
        assert out[1] == 0.0
        assert out[2] == 2.0

    def test_interpolate_distributes_cluster_evenly_when_lrc_durations_uniform(self):
        # When all LRC durations in the cluster are identical, the
        # interpolated times spread evenly across the audio gap.
        lrc = [
            (0.0, 1.0, "a"),
            (1.0, 2.0, "b"),
            (2.0, 3.0, "c"),
            (3.0, 4.0, "d"),
        ]
        assignment: list[float | None] = [0.0, None, None, 9.0]
        out = _interpolate_unanchored(assignment, lrc)
        # b at LRC 1/3 of span -> 3.0; c at LRC 2/3 of span -> 6.0
        assert out[1] == pytest.approx(3.0, abs=0.01)
        assert out[2] == pytest.approx(6.0, abs=0.01)
