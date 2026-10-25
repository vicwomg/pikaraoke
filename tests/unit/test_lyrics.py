"""Unit tests for the LRCLIB lyrics lookup and .ass rendering."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib.lyrics import (
    ACTIVITY_FRAME_S,
    LRC_LEAD_S,
    MIN_ALIGNMENT_AGREEMENT,
    align_lyrics,
    best_alignment,
    lrc_to_ass,
    parse_lrc,
    pick_by_duration,
    search_synced_lyrics,
    shift_ass,
    vocal_activity,
)


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


class TestSearchAndPick:
    def _response(self, results):
        response = MagicMock()
        response.json.return_value = results
        return response

    @patch("pikaraoke.lib.lyrics.requests.get")
    def test_search_keeps_only_synced_results(self, mock_get):
        mock_get.return_value = self._response(
            [
                {"duration": 200, "syncedLyrics": None},
                {"duration": 200, "syncedLyrics": "[00:01.00]x"},
            ]
        )
        assert search_synced_lyrics("A - T") == [{"duration": 200, "syncedLyrics": "[00:01.00]x"}]
        assert mock_get.call_args.kwargs["params"] == {"q": "A - T"}

    @patch("pikaraoke.lib.lyrics.requests.get")
    def test_network_failure_finds_nothing(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("offline")
        assert search_synced_lyrics("A - T") == []

    def test_picks_result_closest_in_length(self):
        results = [
            {"duration": 202, "syncedLyrics": "far"},
            {"duration": 200.5, "syncedLyrics": "near"},
        ]
        assert pick_by_duration(results, 200) == "near"

    def test_rejects_lyrics_timed_to_a_different_edit(self):
        assert pick_by_duration([{"duration": 240, "syncedLyrics": "x"}], 300) is None


class TestOffsetAndShift:
    def test_offset_moves_every_line(self):
        events = _dialogues(lrc_to_ass("[00:10.00]one\n[00:13.00]two", offset=20))
        assert events[0].startswith("Dialogue: 0,0:00:27.00,0:00:33.00,")

    def test_offset_drops_lines_sung_before_the_video_starts(self):
        events = _dialogues(lrc_to_ass("[00:01.00]gone\n[00:20.00]kept", offset=-15))
        assert len(events) == 1
        assert events[0].endswith("kept")

    def test_shift_moves_events_and_leaves_the_rest(self):
        ass = lrc_to_ass("[00:10.00]one")
        shifted = shift_ass(ass, 1.5)
        assert _dialogues(shifted)[0].startswith("Dialogue: 0,0:00:08.50,0:00:21.50,")
        assert shifted.split("[Events]")[0] == ass.split("[Events]")[0]

    def test_shift_earlier_drops_events_pushed_before_the_start(self):
        ass = lrc_to_ass("[00:01.00]gone\n[00:30.00]kept")
        shifted = _dialogues(shift_ass(ass, -12))
        assert len(shifted) == 1
        assert shifted[0].startswith("Dialogue: 0,0:00:15.00,")


def _activity(sung: list[tuple[float, float]], length_s: float) -> list[bool]:
    frames = round(length_s / ACTIVITY_FRAME_S)
    return [any(start <= i * ACTIVITY_FRAME_S < end for start, end in sung) for i in range(frames)]


# Four lines, each sung for about two seconds of its four.
LRC_FOUR = "[00:10.00]a\n[00:14.00]b\n[00:18.00]c\n[00:22.00]d\n[00:26.00]"


class TestAlignment:
    def test_finds_a_longer_intro(self):
        singing = [(t + 37, t + 39.5) for t in (10, 14, 18, 22)]
        offset, agreement = align_lyrics(LRC_FOUR, _activity(singing, 120))
        assert offset == pytest.approx(37, abs=0.5)
        assert agreement > MIN_ALIGNMENT_AGREEMENT

    def test_keeps_lyrics_in_place_when_they_already_fit(self):
        singing = [(t, t + 3.5) for t in (10, 14, 18, 22)]
        offset, _ = align_lyrics(LRC_FOUR, _activity(singing, 60))
        assert offset == pytest.approx(0, abs=0.5)

    def test_low_agreement_without_singing_where_lines_could_land(self):
        _, agreement = align_lyrics(LRC_FOUR, _activity([(100, 140)], 160))
        assert agreement < MIN_ALIGNMENT_AGREEMENT

    def test_best_alignment_takes_the_median_placement_of_agreeing_uploads(self):
        singing = [(t + 30, t + 33.5) for t in (10, 14, 18, 22)]
        uploads = [
            {"trackName": "Song", "syncedLyrics": LRC_FOUR},
            {"trackName": "Song", "syncedLyrics": LRC_FOUR.replace("[00:10.00]", "[00:10.20]")},
            {"trackName": "Song", "syncedLyrics": LRC_FOUR.replace("[00:10.00]", "[00:09.80]")},
        ]
        lrc, offset = best_alignment(uploads, _activity(singing, 120), "Artist - Song")
        # The singing starts 30s later than the lyrics; LRC leads its singing.
        assert 10 + offset == pytest.approx(40 - LRC_LEAD_S, abs=0.5)
        assert lrc in {u["syncedLyrics"] for u in uploads}

    def test_best_alignment_ignores_results_for_other_songs(self):
        singing = [(t, t + 3.5) for t in (10, 14, 18, 22)]
        results = [{"trackName": "Another Song", "syncedLyrics": LRC_FOUR}]
        assert best_alignment(results, _activity(singing, 60), "Artist - Song") is None

    def test_best_alignment_gives_up_on_a_poor_fit(self):
        results = [{"trackName": "Song", "syncedLyrics": LRC_FOUR}]
        assert best_alignment(results, _activity([(100, 140)], 160), "Artist - Song") is None


class TestVocalActivity:
    def _ffmpeg(self, levels):
        lines = []
        for i, level in enumerate(levels):
            lines.append(f"frame:{i} pts:{i * 800} pts_time:{i * 0.1}")
            lines.append(f"lavfi.astats.Overall.RMS_level={level}")
        return subprocess.CompletedProcess([], 0, "\n".join(lines), "")

    @patch("pikaraoke.lib.lyrics.subprocess.run")
    def test_marks_frames_near_the_loud_passages(self, mock_run):
        mock_run.return_value = self._ffmpeg(["-inf", -70, -20, -15, -40, "-inf"])
        assert vocal_activity("v.m4a") == [False, False, True, True, True, False]

    @patch("pikaraoke.lib.lyrics.subprocess.run")
    def test_silence_has_no_activity(self, mock_run):
        mock_run.return_value = self._ffmpeg(["-inf", "-inf"])
        assert vocal_activity("v.m4a") is None

    @patch("pikaraoke.lib.lyrics.subprocess.run")
    def test_ffmpeg_failure_returns_none(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "boom")
        assert vocal_activity("v.m4a") is None
