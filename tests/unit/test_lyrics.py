"""Unit tests for pikaraoke.lib.lyrics."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.lyrics import (
    ASS_MARKER,
    LyricsService,
    Word,
    _ass_header,
    _ass_path,
    _cleanup_yt_subs_and_info,
    _escape_ass,
    _fetch_lrclib,
    _format_ass_time,
    _info_json_path,
    _k_token,
    _lrc_plain_text,
    _lrc_to_ass_line_level,
    _needs_word_level_upgrade,
    _parse_lrc,
    _parse_vtt_cues,
    _pick_best_vtt,
    _read_info_json,
    _title_from_filename,
    _user_owned_ass,
    _vtt_to_ass,
    _words_to_ass_with_k_tags,
    _write_ass_atomic,
)

# ----- LRC parser -----


class TestParseLrc:
    def test_simple(self):
        lrc = "[00:01.00]hello\n[00:03.50]world"
        assert _parse_lrc(lrc) == [(1.0, "hello"), (3.5, "world")]

    def test_multi_time_line(self):
        lrc = "[00:12.34][00:25.67]chorus"
        assert _parse_lrc(lrc) == [(12.34, "chorus"), (25.67, "chorus")]

    def test_fractional_is_decimal_not_centi(self):
        # .5 should be 0.5s, not 0.05s
        assert _parse_lrc("[00:01.5]x") == [(1.5, "x")]

    def test_millisecond_precision(self):
        assert _parse_lrc("[00:01.123]x") == [(1.123, "x")]

    def test_skips_empty_and_metadata_only_lines(self):
        lrc = "[ti:Title]\n[ar:Artist]\n[00:00.00]\n[00:02.00]real"
        assert _parse_lrc(lrc) == [(2.0, "real")]

    def test_sorts_by_time(self):
        lrc = "[00:05.00]b\n[00:01.00]a"
        assert _parse_lrc(lrc) == [(1.0, "a"), (5.0, "b")]

    def test_empty_input(self):
        assert _parse_lrc("") == []


# ----- ASS builders -----


class TestLrcToAssLineLevel:
    def test_produces_header_and_dialogues(self):
        ass = _lrc_to_ass_line_level("[00:01.00]hello\n[00:03.00]world")
        assert ass is not None
        assert "[Script Info]" in ass
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass
        assert "Dialogue:" in ass
        assert ass.count("Dialogue:") == 2

    def test_end_time_is_next_start(self):
        ass = _lrc_to_ass_line_level("[00:01.00]a\n[00:03.00]b")
        lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        assert "0:00:01.00" in lines[0]
        assert "0:00:03.00" in lines[0]  # end of first = start of second
        assert "0:00:03.00" in lines[1]  # start of second

    def test_last_line_holds_for_5s(self):
        ass = _lrc_to_ass_line_level("[00:10.00]last")
        line = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")][0]
        assert "0:00:10.00" in line
        assert "0:00:15.00" in line  # 10 + 5s hold

    def test_empty_lrc_returns_none(self):
        assert _lrc_to_ass_line_level("") is None


class TestFormatAssTime:
    def test_basic(self):
        assert _format_ass_time(0.0) == "0:00:00.00"
        assert _format_ass_time(5.0) == "0:00:05.00"
        assert _format_ass_time(65.5) == "0:01:05.50"

    def test_hours(self):
        assert _format_ass_time(3665.25) == "1:01:05.25"

    def test_negative_clamped(self):
        assert _format_ass_time(-1.0) == "0:00:00.00"


class TestEscapeAss:
    def test_escapes_braces(self):
        assert _escape_ass("hello {world}") == "hello \\{world\\}"

    def test_leaves_normal_text(self):
        assert _escape_ass("plain text") == "plain text"


class TestKToken:
    def test_builds_k_tag(self):
        word = Word(text="hello", start=0.0, end=0.5)
        assert _k_token(word) == "{\\k50}hello"

    def test_minimum_duration_one_cs(self):
        word = Word(text="x", start=1.0, end=1.0)
        assert _k_token(word) == "{\\k1}x"

    def test_rounds_to_centi(self):
        word = Word(text="a", start=0.0, end=0.333)
        assert _k_token(word) == "{\\k33}a"


class TestWordsToAssWithKTags:
    def test_words_fill_line(self):
        lrc = "[00:01.00]hello world"
        words = [
            Word("hello", 1.0, 1.5),
            Word("world", 1.5, 2.0),
        ]
        ass = _words_to_ass_with_k_tags(words, lrc)
        assert "{\\k50}hello {\\k50}world" in ass

    def test_line_without_matching_words_falls_back_to_text(self):
        lrc = "[00:01.00]hello\n[00:20.00]world"
        words = [Word("hello", 1.0, 1.5)]  # no words in second line window
        ass = _words_to_ass_with_k_tags(words, lrc)
        # Second dialogue has raw text, no \k
        dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        assert "{\\k" not in dialogues[1]
        assert "world" in dialogues[1]

    def test_empty_lrc_returns_none(self):
        assert _words_to_ass_with_k_tags([Word("x", 0, 1)], "") is None


class TestLrcPlainText:
    def test_strips_timestamps(self):
        lrc = "[00:01.00]hello\n[00:02.00]world"
        assert _lrc_plain_text(lrc) == "hello\nworld"


# ----- info.json reader -----


class TestReadInfoJson:
    def test_direct_fields(self, tmp_path):
        song = tmp_path / "Song---abc.mp4"
        info = tmp_path / "Song---abc.info.json"
        info.write_text(json.dumps({"track": "T", "artist": "A", "duration": 180}))
        result = _read_info_json(str(song))
        assert result == {"track": "T", "artist": "A", "duration": 180}

    def test_fallback_to_title_split(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        info = tmp_path / "Foo---abc.info.json"
        info.write_text(json.dumps({"title": "Queen - Bohemian Rhapsody"}))
        result = _read_info_json(str(song))
        assert result == {"track": "Bohemian Rhapsody", "artist": "Queen", "duration": None}

    def test_missing_file_returns_none(self, tmp_path):
        song = tmp_path / "NoInfo---x.mp4"
        assert _read_info_json(str(song)) is None

    def test_invalid_json_returns_none(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        info = tmp_path / "Foo---abc.info.json"
        info.write_text("not json {")
        assert _read_info_json(str(song)) is None

    def test_missing_metadata_returns_none(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        info = tmp_path / "Foo---abc.info.json"
        info.write_text(json.dumps({"title": "just a title without separator"}))
        assert _read_info_json(str(song)) is None


# ----- LRCLib client -----


class TestFetchLrclib:
    def test_get_returns_synced(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"syncedLyrics": "[00:01.00]hi"}
        with patch("pikaraoke.lib.lyrics.requests.get", return_value=response):
            assert _fetch_lrclib("T", "A", 180) == "[00:01.00]hi"

    def test_get_without_synced_falls_back_to_search(self):
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"syncedLyrics": None, "plainLyrics": "hi"}
        search_resp = MagicMock(status_code=200)
        search_resp.json.return_value = [
            {"syncedLyrics": None},
            {"syncedLyrics": "[00:02.00]match"},
        ]
        with patch("pikaraoke.lib.lyrics.requests.get", side_effect=[get_resp, search_resp]):
            assert _fetch_lrclib("T", "A", 180) == "[00:02.00]match"

    def test_404_falls_back_to_search(self):
        get_resp = MagicMock(status_code=404)
        get_resp.json.return_value = {}
        search_resp = MagicMock(status_code=200)
        search_resp.json.return_value = [{"syncedLyrics": "[00:03.00]x"}]
        with patch("pikaraoke.lib.lyrics.requests.get", side_effect=[get_resp, search_resp]):
            assert _fetch_lrclib("T", "A", None) == "[00:03.00]x"

    def test_empty_search_returns_none(self):
        get_resp = MagicMock(status_code=404)
        search_resp = MagicMock(status_code=200)
        search_resp.json.return_value = []
        with patch("pikaraoke.lib.lyrics.requests.get", side_effect=[get_resp, search_resp]):
            assert _fetch_lrclib("T", "A", None) is None

    def test_network_error_returns_none(self):
        with patch(
            "pikaraoke.lib.lyrics.requests.get",
            side_effect=requests.ConnectionError(),
        ):
            assert _fetch_lrclib("T", "A", 180) is None

    def test_passes_duration_only_when_set(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"syncedLyrics": "x"}
        with patch("pikaraoke.lib.lyrics.requests.get", return_value=response) as mock_get:
            _fetch_lrclib("T", "A", None)
            params = mock_get.call_args.kwargs["params"]
            assert "duration" not in params


# ----- atomic write -----


class TestWriteAssAtomic:
    def test_writes_file(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        _write_ass_atomic(str(song), "ASS_CONTENT")
        ass_file = tmp_path / "Foo---abc.ass"
        assert ass_file.exists()
        assert ass_file.read_text(encoding="utf-8") == "ASS_CONTENT"

    def test_overwrites_existing(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        ass_file = tmp_path / "Foo---abc.ass"
        ass_file.write_text("OLD")
        _write_ass_atomic(str(song), "NEW")
        assert ass_file.read_text(encoding="utf-8") == "NEW"


# ----- LyricsService orchestration -----


@pytest.fixture
def song_and_info(tmp_path):
    song = tmp_path / "Foo---abc.mp4"
    song.write_text("fake mp4")
    info = tmp_path / "Foo---abc.info.json"
    info.write_text(json.dumps({"track": "T", "artist": "A", "duration": 180}))
    return str(song)


class TestLyricsServiceFetchAndConvert:
    def test_writes_line_level_ass_and_removes_info_json(self, song_and_info, tmp_path):
        service = LyricsService(str(tmp_path), EventSystem())
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]hello\n[00:03.00]world",
        ):
            service.fetch_and_convert(song_and_info)
        ass = tmp_path / "Foo---abc.ass"
        assert ass.exists()
        assert "Dialogue:" in ass.read_text(encoding="utf-8")
        assert not (tmp_path / "Foo---abc.info.json").exists()

    def test_no_info_json_and_no_vtt_skips_silently(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        song.write_text("fake")
        service = LyricsService(str(tmp_path), EventSystem())
        with patch("pikaraoke.lib.lyrics._fetch_lrclib") as mock_fetch:
            service.fetch_and_convert(str(song))
            mock_fetch.assert_not_called()
        assert not (tmp_path / "Foo---abc.ass").exists()

    def test_aligner_invoked_when_provided(self, song_and_info, tmp_path):
        aligner = MagicMock()
        aligner.align.return_value = [Word("hello", 1.0, 1.5)]
        service = LyricsService(str(tmp_path), EventSystem(), aligner=aligner)
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]hello",
        ), patch("pikaraoke.lib.lyrics.Thread") as mock_thread:
            service.fetch_and_convert(song_and_info)
            mock_thread.assert_called_once()
            # Run the target synchronously to verify upgrade path
            target = mock_thread.call_args.kwargs["target"]
            args = mock_thread.call_args.kwargs["args"]
            target(*args)
        aligner.align.assert_called_once()
        ass_text = (tmp_path / "Foo---abc.ass").read_text(encoding="utf-8")
        assert "{\\k50}hello" in ass_text

    def test_unexpected_exception_swallowed(self, song_and_info, tmp_path):
        service = LyricsService(str(tmp_path), EventSystem())
        with patch("pikaraoke.lib.lyrics._read_info_json", side_effect=RuntimeError("boom")):
            # Must not raise - event listener context
            service.fetch_and_convert(song_and_info)


# ----- path helpers -----


class TestPathHelpers:
    def test_ass_path_replaces_extension(self):
        assert _ass_path("/a/Song---x.mp4") == "/a/Song---x.ass"
        assert _ass_path("/a/Song---x.webm") == "/a/Song---x.ass"

    def test_info_json_path(self):
        assert _info_json_path("/a/Song---x.mp4") == "/a/Song---x.info.json"


# ----- VTT parser -----


VTT_SAMPLE = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
Hello world

00:00:03.500 --> 00:00:05.000
<c>Second</c> line
"""


class TestParseVttCues:
    def test_basic_cues(self):
        cues = _parse_vtt_cues(VTT_SAMPLE)
        assert cues == [
            (1.0, 3.0, "Hello world"),
            (3.5, 5.0, "Second line"),
        ]

    def test_strips_inline_tags(self):
        vtt = (
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n"
            "<00:00:00.100><c> hello</c><00:00:00.500><c> world</c>\n"
        )
        assert _parse_vtt_cues(vtt) == [(0.0, 1.0, "hello world")]

    def test_multiline_cue_joined_with_space(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nline one\nline two\n"
        assert _parse_vtt_cues(vtt) == [(1.0, 3.0, "line one line two")]

    def test_rolling_window_collapsed(self):
        # YouTube auto-captions pattern: each cue repeats the previous text + new word.
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\nhello\n\n"
            "00:00:01.000 --> 00:00:03.000\nhello world\n\n"
            "00:00:02.000 --> 00:00:04.000\nhello world how\n"
        )
        cues = _parse_vtt_cues(vtt)
        assert cues == [(0.0, 4.0, "hello world how")]

    def test_empty_input(self):
        assert _parse_vtt_cues("WEBVTT\n") == []


class TestVttToAss:
    def test_produces_ass_with_marker(self):
        ass = _vtt_to_ass(VTT_SAMPLE)
        assert ass is not None
        assert ASS_MARKER in ass
        assert ass.count("Dialogue:") == 2

    def test_empty_vtt_returns_none(self):
        assert _vtt_to_ass("WEBVTT\n") is None


class TestPickBestVtt:
    def test_prefers_shorter_lang_code(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.pl.vtt").write_text("WEBVTT")
        (tmp_path / "Foo---abc.pl-PL.vtt").write_text("WEBVTT")
        picked = _pick_best_vtt(str(song))
        assert picked is not None
        assert picked.endswith(".pl.vtt")

    def test_prefers_manual_over_auto(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.en.vtt").write_text("WEBVTT")
        (tmp_path / "Foo---abc.en-orig.vtt").write_text("WEBVTT")
        picked = _pick_best_vtt(str(song))
        assert picked is not None
        assert picked.endswith(".en.vtt")

    def test_none_when_no_vtt(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        assert _pick_best_vtt(str(song)) is None

    def test_ignores_vtt_of_other_songs(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Other---xyz.en.vtt").write_text("WEBVTT")
        assert _pick_best_vtt(str(song)) is None


# ----- marker + ownership -----


class TestAssHeader:
    def test_contains_marker(self):
        assert ASS_MARKER in _ass_header()


class TestUserOwnedAss:
    def test_false_when_no_ass(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        assert _user_owned_ass(str(song)) is False

    def test_false_when_ass_has_marker(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.ass").write_text(f"[Script Info]\nTitle: {ASS_MARKER}\n")
        assert _user_owned_ass(str(song)) is False

    def test_true_when_ass_missing_marker(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.ass").write_text("[Script Info]\nTitle: My Aegisub file\n")
        assert _user_owned_ass(str(song)) is True


# ----- cleanup -----


class TestCleanupYtSubsAndInfo:
    def test_removes_vtt_and_info_json(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.en.vtt").write_text("x")
        (tmp_path / "Foo---abc.pl.vtt").write_text("x")
        (tmp_path / "Foo---abc.info.json").write_text("{}")
        # Keep the .ass and unrelated files
        (tmp_path / "Foo---abc.ass").write_text("ASS")
        (tmp_path / "Unrelated---xyz.en.vtt").write_text("x")

        _cleanup_yt_subs_and_info(str(song))

        assert not (tmp_path / "Foo---abc.en.vtt").exists()
        assert not (tmp_path / "Foo---abc.pl.vtt").exists()
        assert not (tmp_path / "Foo---abc.info.json").exists()
        assert (tmp_path / "Foo---abc.ass").exists()
        assert (tmp_path / "Unrelated---xyz.en.vtt").exists()


# ----- LyricsService new flow (VTT + LRCLib) -----


class TestLyricsServiceNewFlow:
    def _setup(self, tmp_path, *, with_vtt=False, with_info=True):
        song = tmp_path / "Foo---abc.mp4"
        song.write_text("fake mp4")
        if with_info:
            (tmp_path / "Foo---abc.info.json").write_text(
                json.dumps({"track": "T", "artist": "A", "duration": 180})
            )
        if with_vtt:
            (tmp_path / "Foo---abc.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nvtt line\n"
            )
        return str(song)

    def test_vtt_only_writes_ass_from_vtt(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=True, with_info=False)
        service = LyricsService(str(tmp_path), EventSystem())
        service.fetch_and_convert(song)
        ass = (tmp_path / "Foo---abc.ass").read_text(encoding="utf-8")
        assert "vtt line" in ass
        assert ASS_MARKER in ass
        # VTT cleaned up
        assert not (tmp_path / "Foo---abc.en.vtt").exists()

    def test_lrclib_overrides_vtt(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=True, with_info=True)
        service = LyricsService(str(tmp_path), EventSystem())
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]lrclib line",
        ):
            service.fetch_and_convert(song)
        ass = (tmp_path / "Foo---abc.ass").read_text(encoding="utf-8")
        assert "lrclib line" in ass
        assert "vtt line" not in ass
        assert not (tmp_path / "Foo---abc.en.vtt").exists()
        assert not (tmp_path / "Foo---abc.info.json").exists()

    def test_vtt_only_when_lrclib_misses(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=True, with_info=True)
        service = LyricsService(str(tmp_path), EventSystem())
        with patch("pikaraoke.lib.lyrics._fetch_lrclib", return_value=None):
            service.fetch_and_convert(song)
        ass = (tmp_path / "Foo---abc.ass").read_text(encoding="utf-8")
        assert "vtt line" in ass

    def test_user_aegisub_is_not_overwritten(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=True, with_info=True)
        user_ass = "[Script Info]\nTitle: My Aegisub\n\nDialogue: manually crafted\n"
        (tmp_path / "Foo---abc.ass").write_text(user_ass)
        service = LyricsService(str(tmp_path), EventSystem())
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]lrclib line",
        ) as mock_fetch:
            service.fetch_and_convert(song)
            mock_fetch.assert_not_called()
        assert (tmp_path / "Foo---abc.ass").read_text() == user_ass

    def test_previous_auto_ass_is_overwritten(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=False, with_info=True)
        # Previously auto-generated .ass (has marker) - should be replaced by fresh LRCLib.
        (tmp_path / "Foo---abc.ass").write_text(f"[Script Info]\nTitle: {ASS_MARKER}\nstale\n")
        service = LyricsService(str(tmp_path), EventSystem())
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]fresh",
        ):
            service.fetch_and_convert(song)
        assert "fresh" in (tmp_path / "Foo---abc.ass").read_text()

    def test_no_source_means_no_ass(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=False, with_info=True)
        service = LyricsService(str(tmp_path), EventSystem())
        with patch("pikaraoke.lib.lyrics._fetch_lrclib", return_value=None):
            service.fetch_and_convert(song)
        assert not (tmp_path / "Foo---abc.ass").exists()
        assert not (tmp_path / "Foo---abc.info.json").exists()

    def test_itunes_fallback_when_lrclib_misses(self, tmp_path):
        # info.json has noisy fields; first LRCLib call fails. iTunes returns
        # canonical metadata; second LRCLib call (with clean fields) succeeds.
        song = tmp_path / "Foo---abc.mp4"
        song.write_text("fake")
        (tmp_path / "Foo---abc.info.json").write_text(
            json.dumps(
                {
                    "track": "Stan (Long Version) ft. Dido",
                    "artist": "Eminem",
                    "duration": 489,
                }
            )
        )
        service = LyricsService(str(tmp_path), EventSystem())
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            side_effect=[None, "[00:01.00]clean line"],
        ) as mock_fetch, patch(
            "pikaraoke.lib.lyrics.resolve_metadata",
            return_value={"artist": "Eminem", "track": "Stan (feat. Dido)"},
        ) as mock_resolve:
            service.fetch_and_convert(str(song))
        assert mock_fetch.call_count == 2
        # Second call uses canonical fields from iTunes.
        second_call_args = mock_fetch.call_args_list[1].args
        assert second_call_args[0] == "Stan (feat. Dido)"
        assert second_call_args[1] == "Eminem"
        mock_resolve.assert_called_once()
        ass = (tmp_path / "Foo---abc.ass").read_text(encoding="utf-8")
        assert "clean line" in ass

    def test_itunes_fallback_skipped_when_lrclib_hits(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        song.write_text("fake")
        (tmp_path / "Foo---abc.info.json").write_text(
            json.dumps({"track": "T", "artist": "A", "duration": 180})
        )
        service = LyricsService(str(tmp_path), EventSystem())
        with patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]first hit",
        ), patch("pikaraoke.lib.lyrics.resolve_metadata") as mock_resolve:
            service.fetch_and_convert(str(song))
            mock_resolve.assert_not_called()

    def test_itunes_fallback_returns_none(self, tmp_path):
        # LRCLib misses; iTunes also misses -> no .ass written from LRC.
        song = tmp_path / "Foo---abc.mp4"
        song.write_text("fake")
        (tmp_path / "Foo---abc.info.json").write_text(
            json.dumps({"track": "T", "artist": "A", "duration": 180})
        )
        service = LyricsService(str(tmp_path), EventSystem())
        with patch("pikaraoke.lib.lyrics._fetch_lrclib", return_value=None), patch(
            "pikaraoke.lib.lyrics.resolve_metadata", return_value=None
        ):
            service.fetch_and_convert(str(song))
        assert not (tmp_path / "Foo---abc.ass").exists()

    def test_aligner_only_runs_when_lrclib_hit(self, tmp_path):
        song = self._setup(tmp_path, with_vtt=True, with_info=True)
        aligner = MagicMock()
        aligner.align.return_value = [Word("x", 0, 1)]
        service = LyricsService(str(tmp_path), EventSystem(), aligner=aligner)
        with patch("pikaraoke.lib.lyrics._fetch_lrclib", return_value=None), patch(
            "pikaraoke.lib.lyrics.Thread"
        ) as mock_thread:
            service.fetch_and_convert(song)
            mock_thread.assert_not_called()
        aligner.align.assert_not_called()


# ----- Title-from-filename + reprocess helpers -----


class TestTitleFromFilename:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/songs/Eminem - Stan---gOMhN-hfMtY.mp4", "Eminem - Stan"),
            ("/songs/Queen - Bohemian [dQw4w9WgXcQ].mp4", "Queen - Bohemian"),
            ("/songs/Queen - Bohemian [dQw4w9WgXcQ].webm", "Queen - Bohemian"),
            ("/Bare Title.mp4", "Bare Title"),
            ("no_id_at_all---notenough.mp4", "no_id_at_all---notenough"),
        ],
    )
    def test_strips_youtube_id(self, path, expected):
        assert _title_from_filename(path) == expected


class TestNeedsWordLevelUpgrade:
    def test_no_ass_file(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        assert _needs_word_level_upgrade(str(song)) is False

    def test_line_level_auto_ass_is_candidate(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.ass").write_text(
            f"[Script Info]\nTitle: {ASS_MARKER}\n\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello\n"
        )
        assert _needs_word_level_upgrade(str(song)) is True

    def test_already_word_level_skipped(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.ass").write_text(
            f"[Script Info]\nTitle: {ASS_MARKER}\n\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{{\\k50}}hi\n"
        )
        assert _needs_word_level_upgrade(str(song)) is False

    def test_user_owned_ass_skipped(self, tmp_path):
        song = tmp_path / "Foo---abc.mp4"
        (tmp_path / "Foo---abc.ass").write_text("[Script Info]\nTitle: Aegisub File\n")
        assert _needs_word_level_upgrade(str(song)) is False


class TestReprocessLibrary:
    def _make_line_level_song(self, tmp_path, name="Eminem - Stan---abcdefghij1"):
        song = tmp_path / f"{name}.mp4"
        song.write_text("fake")
        (tmp_path / f"{name}.ass").write_text(
            f"[Script Info]\nTitle: {ASS_MARKER}\n\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello world\n"
        )
        return str(song)

    def test_no_aligner_is_noop(self, tmp_path):
        song = self._make_line_level_song(tmp_path)
        service = LyricsService(str(tmp_path), EventSystem(), aligner=None)
        assert service.reprocess_library([song]) == 0

    def test_no_candidates_returns_zero(self, tmp_path):
        # Song with .ass that already has \k tags.
        song = tmp_path / "Foo---abcdefghij1.mp4"
        song.write_text("fake")
        (tmp_path / "Foo---abcdefghij1.ass").write_text(
            f"[Script Info]\nTitle: {ASS_MARKER}\n\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\k50}hi\n"
        )
        service = LyricsService(str(tmp_path), EventSystem(), aligner=MagicMock())
        assert service.reprocess_library([str(song)]) == 0

    def test_candidates_spawn_background_thread(self, tmp_path):
        song = self._make_line_level_song(tmp_path)
        service = LyricsService(str(tmp_path), EventSystem(), aligner=MagicMock())
        with patch("pikaraoke.lib.lyrics.Thread") as mock_thread:
            n = service.reprocess_library([song])
        assert n == 1
        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs["daemon"] is True

    def test_reprocess_one_happy_path(self, tmp_path):
        song = self._make_line_level_song(tmp_path, "Eminem - Stan---abcdefghij1")
        aligner = MagicMock()
        aligner.align.return_value = [Word("hello", 1.0, 1.5), Word("world", 1.5, 2.0)]
        service = LyricsService(str(tmp_path), EventSystem(), aligner=aligner)
        with patch(
            "pikaraoke.lib.lyrics.resolve_metadata",
            return_value={"artist": "Eminem", "track": "Stan"},
        ), patch(
            "pikaraoke.lib.lyrics._fetch_lrclib",
            return_value="[00:01.00]hello world",
        ):
            service._reprocess_one(song)
        ass_text = (tmp_path / "Eminem - Stan---abcdefghij1.ass").read_text()
        # Must now contain \k tags from the aligner output.
        assert "\\k" in ass_text
        assert "hello" in ass_text and "world" in ass_text

    def test_reprocess_one_skips_on_itunes_miss(self, tmp_path):
        song = self._make_line_level_song(tmp_path)
        aligner = MagicMock()
        service = LyricsService(str(tmp_path), EventSystem(), aligner=aligner)
        with patch("pikaraoke.lib.lyrics.resolve_metadata", return_value=None):
            service._reprocess_one(song)
        aligner.align.assert_not_called()

    def test_reprocess_one_skips_on_lrclib_miss(self, tmp_path):
        song = self._make_line_level_song(tmp_path)
        aligner = MagicMock()
        service = LyricsService(str(tmp_path), EventSystem(), aligner=aligner)
        with patch(
            "pikaraoke.lib.lyrics.resolve_metadata",
            return_value={"artist": "Eminem", "track": "Stan"},
        ), patch("pikaraoke.lib.lyrics._fetch_lrclib", return_value=None):
            service._reprocess_one(song)
        aligner.align.assert_not_called()

    def test_reprocess_batch_continues_after_one_failure(self, tmp_path):
        song_good = self._make_line_level_song(tmp_path, "Good - Song---abcdefghij1")
        song_bad = self._make_line_level_song(tmp_path, "Bad - Song---abcdefghij2")
        service = LyricsService(str(tmp_path), EventSystem(), aligner=MagicMock())
        call_order = []

        def side_effect(p):
            call_order.append(p)
            if p == song_bad:
                raise RuntimeError("boom")

        with patch.object(service, "_reprocess_one", side_effect=side_effect):
            service._reprocess_batch([song_bad, song_good])
        assert call_order == [song_bad, song_good]  # both attempted

    def test_reprocess_one_skips_if_no_longer_candidate(self, tmp_path):
        # Pretend someone upgraded the .ass between scan and processing.
        song = tmp_path / "Foo---abcdefghij1.mp4"
        song.write_text("fake")
        (tmp_path / "Foo---abcdefghij1.ass").write_text(
            f"[Script Info]\nTitle: {ASS_MARKER}\n\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\k50}already\n"
        )
        aligner = MagicMock()
        service = LyricsService(str(tmp_path), EventSystem(), aligner=aligner)
        with patch("pikaraoke.lib.lyrics.resolve_metadata") as mock_resolve:
            service._reprocess_one(str(song))
            mock_resolve.assert_not_called()
