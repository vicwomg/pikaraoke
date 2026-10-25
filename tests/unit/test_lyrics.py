"""Unit tests for the LRCLIB lyrics lookup and .ass rendering."""

from unittest.mock import MagicMock, patch

import requests

from pikaraoke.lib.lyrics import fetch_synced_lyrics, lrc_to_ass, parse_lrc


def _dialogues(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


class TestParseLrc:
    def test_reads_lines_in_time_order(self):
        lrc = "[00:12.50]second\n[00:01.00]first\n[ti:Title]\nnot a lyric"
        assert parse_lrc(lrc) == [(1.0, "first"), (12.5, "second")]

    def test_expands_a_line_written_under_several_timestamps(self):
        assert parse_lrc("[00:10.00][01:10.00]chorus") == [(10.0, "chorus"), (70.0, "chorus")]

    def test_keeps_blank_lines_as_stops(self):
        assert parse_lrc("[00:01.00]sung\n[00:04.00]") == [(1.0, "sung"), (4.0, "")]


class TestLrcToAss:
    def test_alternates_rows_and_shows_next_line_while_current_is_sung(self):
        events = _dialogues(lrc_to_ass("[00:10.00]one\n[00:13.00]two\n[00:16.00]three"))

        assert events[0].startswith("Dialogue: 0,0:00:07.00,0:00:13.00,Upper,")
        # "two" follows "one" without a gap, so it appears as "one" starts.
        assert events[1].startswith("Dialogue: 0,0:00:10.00,0:00:16.00,Lower,")
        assert events[2].startswith("Dialogue: 0,0:00:13.00,")
        assert ",Upper," in events[2]

    def test_holds_the_sweep_until_the_line_is_sung(self):
        events = _dialogues(lrc_to_ass("[00:10.00]one\n[00:13.00]two"))
        assert events[0].endswith("{\\k300}{\\kf300}one")
        assert events[1].endswith("{\\k300}{\\kf1000}two")

    def test_does_not_hold_a_line_across_an_instrumental_break(self):
        events = _dialogues(lrc_to_ass("[00:10.00]before\n[01:00.00]after"))
        assert events[0].startswith("Dialogue: 0,0:00:07.00,0:00:20.00,")
        # Nothing to follow on from, so it gets a lead-in of its own.
        assert events[1].startswith("Dialogue: 0,0:00:57.00,")

    def test_blank_line_ends_the_line_before_it(self):
        events = _dialogues(lrc_to_ass("[00:10.00]sung\n[00:12.00]\n[00:30.00]next"))
        assert len(events) == 2
        assert events[0].startswith("Dialogue: 0,0:00:07.00,0:00:12.00,")

    def test_escapes_override_characters(self):
        events = _dialogues(lrc_to_ass("[00:05.00]a {b} c\\d"))
        assert events[0].endswith("a (b) c/d")


class TestFetchSyncedLyrics:
    def _response(self, results):
        response = MagicMock()
        response.json.return_value = results
        return response

    @patch("pikaraoke.lib.lyrics.requests.get")
    def test_picks_synced_result_closest_in_length(self, mock_get):
        mock_get.return_value = self._response(
            [
                {"duration": 200, "syncedLyrics": None, "artistName": "A", "trackName": "T"},
                {
                    "duration": 202,
                    "syncedLyrics": "[00:01.00]far",
                    "artistName": "A",
                    "trackName": "T",
                },
                {
                    "duration": 200.5,
                    "syncedLyrics": "[00:01.00]near",
                    "artistName": "A",
                    "trackName": "T",
                },
            ]
        )
        assert fetch_synced_lyrics("A - T", 200) == "[00:01.00]near"
        assert mock_get.call_args.kwargs["params"] == {"q": "A - T"}

    @patch("pikaraoke.lib.lyrics.requests.get")
    def test_rejects_lyrics_timed_to_a_different_edit(self, mock_get):
        mock_get.return_value = self._response(
            [{"duration": 240, "syncedLyrics": "[00:01.00]x", "artistName": "A", "trackName": "T"}]
        )
        assert fetch_synced_lyrics("A - T", 300) is None

    @patch("pikaraoke.lib.lyrics.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("offline")
        assert fetch_synced_lyrics("A - T", 200) is None
