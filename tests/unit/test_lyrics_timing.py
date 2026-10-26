"""Unit tests for timing plain lyrics by recognizing the sung words."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.lyrics import parse_lrc
from pikaraoke.lib.lyrics_timing import (
    lead_vocal_left_in,
    time_plain_lyrics,
    transcribe_words,
)


def _heard(*phrases: tuple[float, str]) -> list[tuple[str, float]]:
    """Recognized words, a phrase's words spaced 0.3s apart from its start."""
    words = []
    for start, phrase in phrases:
        tokens = phrase.split() if phrase.isascii() else list(phrase)
        words.extend((token, start + 0.3 * i) for i, token in enumerate(tokens))
    return words


def _starts(lrc: str) -> list[float]:
    return [start for start, _ in parse_lrc(lrc)]


class TestTimePlainLyrics:
    def test_lines_start_where_their_words_are_heard(self):
        lrc = time_plain_lyrics(
            "hello darkness my old friend\nI've come to talk with you again",
            _heard(
                (12.0, "hello darkness my old friend"), (16.0, "ive come to talk with you again")
            ),
        )
        assert _starts(lrc) == pytest.approx([12.0, 16.0])

    def test_traditional_lyrics_match_simplified_recognition(self):
        lrc = time_plain_lyrics(
            "還讓我慌慌張張\n沒有了方向",
            _heard((5.0, "还让我慌慌张张"), (9.0, "没有了方向")),
        )
        assert _starts(lrc) == pytest.approx([5.0, 9.0])
        assert "還讓我慌慌張張" in lrc

    def test_each_chorus_is_timed_to_its_own_singing(self):
        lrc = time_plain_lyrics(
            "sugar yes please\nverse line here now\nsugar yes please",
            _heard(
                (30.0, "sugar yes please"),
                (40.0, "verse line here now"),
                (60.0, "sugar yes please"),
            ),
        )
        assert _starts(lrc) == pytest.approx([30.0, 40.0, 60.0])

    def test_a_spoken_intro_is_not_taken_for_the_lyrics(self):
        lrc = time_plain_lyrics(
            "I'm hurting baby I'm broken down\nI need your loving I need it now",
            _heard(
                (2.0, "it's december we're going to hit every wedding baby"),
                (42.0, "im hurting baby im broken down"),
                (47.0, "i need your loving i need it now"),
            ),
        )
        assert _starts(lrc)[0] == pytest.approx(42.0)

    def test_a_misheard_opening_is_dated_back_to_where_it_was_sung(self):
        lrc = time_plain_lyrics("你讓我跌跌撞撞", _heard((68.0, "你上我跌跌撞撞")))
        assert _starts(lrc) == pytest.approx([68.0])

    def test_a_misheard_line_after_a_break_starts_where_singing_resumes(self):
        lrc = time_plain_lyrics(
            "first line sung here\nwhere the break ends\nthen this line follows",
            _heard(
                (10.0, "first line sung here"),
                (100.0, "wear a brick bends"),
                (104.0, "then this line follows"),
            ),
        )
        assert _starts(lrc) == pytest.approx([10.0, 100.0, 104.0])

    def test_too_little_heard_to_trust(self):
        assert (
            time_plain_lyrics(
                "one two three\nfour five six\nseven eight nine", _heard((5.0, "one two three"))
            )
            is None
        )


class TestTranscribeWords:
    def _process(self, returncode: int, output: str) -> MagicMock:
        process = MagicMock(returncode=returncode)
        process.communicate.return_value = (output, "recognizer log")
        return process

    @patch("pikaraoke.lib.lyrics_timing.use_spare_capacity")
    @patch("pikaraoke.lib.lyrics_timing.subprocess.Popen")
    def test_reads_the_words_the_recognizer_prints(self, mock_popen, mock_spare):
        mock_popen.return_value = self._process(0, '[["hello", 1.5], ["你", 2.0]]')
        assert transcribe_words("/songs/a.vocals.m4a") == [("hello", 1.5), ("你", 2.0)]
        assert mock_popen.call_args.args[0][-2] == "/songs/a.vocals.m4a"

    @patch("pikaraoke.lib.lyrics_timing.use_spare_capacity")
    @patch("pikaraoke.lib.lyrics_timing.subprocess.Popen")
    def test_recognizer_failure_gives_nothing(self, mock_popen, mock_spare):
        mock_popen.return_value = self._process(1, "")
        assert transcribe_words("/songs/a.vocals.m4a") is None


class TestLeadVocalLeftIn:
    def test_line_after_line_of_lyrics_is_a_lead_left_in(self):
        heard = _heard(
            *((90.0 + 4 * i, line) for i, line in enumerate(["要是能重来", "我要选李白"] * 3)),
            (130.0, "几百年前做的好坏"),
            (134.0, "没那么多人猜"),
            (138.0, "至少我还能写写诗来澎湃"),
        )
        assert lead_vocal_left_in(heard) is True

    def test_a_stock_phrase_invented_in_plain_music_is_not(self):
        assert lead_vocal_left_in(_heard((120.0, "ご視聴ありがとうございました"))) is False
        assert lead_vocal_left_in(_heard(*((30.0 * i, "thank you") for i in range(8)))) is False

    @patch("pikaraoke.lib.lyrics_timing.use_spare_capacity")
    @patch("pikaraoke.lib.lyrics_timing.subprocess.Popen")
    def test_listening_in_music_is_asked_of_the_recognizer(self, mock_popen, mock_spare):
        process = MagicMock(returncode=0)
        process.communicate.return_value = ("[]", "")
        mock_popen.return_value = process
        transcribe_words("/songs/a.instrumental.m4a", in_music=True)
        assert mock_popen.call_args.args[0][-1] == "in-music"
