"""Unit tests for background song preparation."""

import os
from unittest.mock import patch

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.lyrics import APPROXIMATE_TIMING_NOTE
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.song_preparer import SongPreparer

LRC = "[00:01.00]hello"


def _name(path: str, remove_youtube_id: bool = False) -> str:
    return os.path.basename(path).rsplit("---", 1)[0]


@pytest.fixture(autouse=True)
def no_recognizer():
    """Keep the real recognizer out of tests that do not ask for it."""
    with patch("pikaraoke.lib.song_preparer.is_transcription_available", return_value=False):
        yield


@pytest.fixture
def prefs(tmp_path):
    return PreferenceManager(str(tmp_path / "config.ini"))


@pytest.fixture
def events():
    return EventSystem()


@pytest.fixture
def preparer(events, prefs):
    return SongPreparer(events, prefs, _name)


def _run_pending(preparer: SongPreparer) -> None:
    """Drain the queue the way the worker greenlet would, without spawning it."""
    while not preparer._pending.empty():
        preparer._prepare_next()


class TestPrepare:
    def test_nothing_queued_when_both_features_are_off(self, preparer):
        preparer.prepare("/songs/a.mp4")
        assert preparer._pending.empty()

    @patch("pikaraoke.lib.song_preparer.search_lyrics", side_effect=RuntimeError("boom"))
    def test_one_failing_song_does_not_stop_the_next(self, mock_fetch, preparer, prefs, tmp_path):
        prefs.set("auto_lyrics", True)
        first = str(tmp_path / "Artist - One---abcdefghijk.mp4")
        second = str(tmp_path / "Artist - Two---abcdefghijk.mp4")
        preparer.prepare(first)
        preparer.prepare(second)
        _run_pending(preparer)
        assert mock_fetch.call_count == 2
        assert not preparer._waiting

    def test_a_song_waits_in_line_only_once(self, preparer, prefs):
        prefs.set("auto_lyrics", True)
        preparer.prepare("/songs/a.mp4")
        preparer.prepare("/songs/a.mp4")
        assert preparer._pending.qsize() == 1

    @patch("pikaraoke.lib.song_preparer.is_vocal_removal_available", return_value=False)
    def test_vocal_removal_needs_the_extra_installed(self, mock_available, preparer, prefs):
        prefs.set("vocal_removal", True)
        preparer.prepare("/songs/a.mp4")
        assert preparer._pending.empty()

    @patch("pikaraoke.lib.song_preparer.search_lyrics", return_value=[])
    def test_a_lyrics_miss_is_not_looked_up_again(self, mock_fetch, preparer, prefs, tmp_path):
        prefs.set("auto_lyrics", True)
        song = str(tmp_path / "Artist - Title---abcdefghijk.mp4")
        preparer.prepare(song)
        _run_pending(preparer)
        preparer.prepare(song)
        assert preparer._pending.empty()
        mock_fetch.assert_called_once()

    @patch("pikaraoke.lib.song_preparer.is_vocal_removal_available", return_value=True)
    def test_a_feature_switched_on_later_still_reaches_a_prepared_song(
        self, mock_available, preparer, prefs, tmp_path
    ):
        prefs.set("auto_lyrics", True)
        song = str(tmp_path / "Karaoke Song---abcdefghijk.mp4")
        preparer.prepare(song)
        _run_pending(preparer)
        prefs.set("vocal_removal", True)
        preparer.prepare(song)
        assert preparer._pending.qsize() == 1


class TestProgress:
    def test_a_song_waiting_its_turn(self, preparer, prefs):
        prefs.set("auto_lyrics", True)
        preparer.prepare("/songs/a.mp4")
        assert preparer.progress("/songs/a.mp4") == {"task": "waiting", "remaining_s": None}
        assert preparer.progress("/songs/other.mp4") is None

    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_task_under_way_is_announced_and_cleared(self, mock_duration, preparer, events):
        updates = []
        events.on("preparation_update", lambda: updates.append(preparer.progress("/s/a.mp4")))

        preparer._set_working("vocals", "/s/a.mp4")
        preparer._set_working(None, "/s/a.mp4")

        assert updates == [{"task": "vocals", "remaining_s": None}, None]

    @patch("pikaraoke.lib.song_preparer.time.monotonic")
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_separation_estimates_from_the_last_one_timed(
        self, mock_duration, mock_clock, preparer, tmp_path
    ):
        song = str(tmp_path / "Title---abcdefghijk.mp4")
        mock_clock.return_value = 1000.0
        preparer._set_working("vocals", song)
        mock_clock.return_value = 1160.0  # 160s for 200s of song
        with patch("pikaraoke.lib.song_preparer.separate_vocals", return_value=True):
            preparer._add_instrumental(song)

        other = str(tmp_path / "Other---abcdefghijk.mp4")
        preparer._set_working("vocals", other)
        mock_clock.return_value = 1200.0

        assert preparer.progress(other) == {"task": "vocals", "remaining_s": 120}


class TestAddLyrics:
    RESULTS = [{"duration": 200.0, "syncedLyrics": LRC}]

    @patch("pikaraoke.lib.song_preparer.search_lyrics", return_value=RESULTS)
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_writes_subtitle_and_announces_it(
        self, mock_duration, mock_search, preparer, events, tmp_path
    ):
        song = tmp_path / "Artist - Title (Official Video)---abcdefghijk.mp4"
        song.write_bytes(b"video")
        ready = []
        events.on("lyrics_ready", ready.append)

        preparer._add_lyrics(str(song))

        mock_search.assert_called_once_with("Artist - Title")
        assert (
            "{\\kf" in (tmp_path / "Artist - Title (Official Video)---abcdefghijk.ass").read_text()
        )
        assert ready == [str(song)]

    @patch("pikaraoke.lib.song_preparer.best_alignment")
    @patch("pikaraoke.lib.song_preparer.search_lyrics", return_value=RESULTS)
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_same_length_lyrics_are_used_as_timed_even_with_vocals(
        self, mock_duration, mock_search, mock_align, preparer, tmp_path
    ):
        (tmp_path / "Artist - Title---abcdefghijk.vocals.m4a").write_bytes(b"aac")
        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))
        mock_align.assert_not_called()
        assert (tmp_path / "Artist - Title---abcdefghijk.ass").exists()

    @patch("pikaraoke.lib.song_preparer.search_lyrics", return_value=RESULTS)
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=260.0)
    def test_without_vocals_lyrics_of_another_length_are_not_trusted(
        self, mock_duration, mock_search, preparer, tmp_path
    ):
        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))
        assert not (tmp_path / "Artist - Title---abcdefghijk.ass").exists()

    @patch("pikaraoke.lib.song_preparer.best_alignment", return_value=(LRC, 30.0))
    @patch("pikaraoke.lib.song_preparer.vocal_activity", return_value=[True, False])
    @patch("pikaraoke.lib.song_preparer.search_lyrics", return_value=RESULTS)
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=260.0)
    def test_with_vocals_lyrics_of_another_length_are_aligned_to_the_singing(
        self, mock_duration, mock_search, mock_activity, mock_align, preparer, tmp_path
    ):
        (tmp_path / "Artist - Title---abcdefghijk.vocals.m4a").write_bytes(b"aac")

        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))

        mock_align.assert_called_once_with(self.RESULTS, [True, False], "Artist - Title")
        ass = (tmp_path / "Artist - Title---abcdefghijk.ass").read_text()
        # The line sung at 1s lands at 31s, shown from its 3s lead-in.
        assert "Dialogue: 0,0:00:28.00,0:00:41.00," in ass
        assert APPROXIMATE_TIMING_NOTE in ass

    @patch("pikaraoke.lib.song_preparer.is_transcription_available", return_value=True)
    @patch("pikaraoke.lib.song_preparer.transcribe_words", return_value=[("hello", 31.0)])
    @patch("pikaraoke.lib.song_preparer.vocal_activity", return_value=[True, False])
    @patch(
        "pikaraoke.lib.song_preparer.search_lyrics",
        return_value=[{"trackName": "Title", "plainLyrics": "hello"}],
    )
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=260.0)
    def test_untimed_lyrics_are_timed_by_ear_and_marked(
        self,
        mock_duration,
        mock_search,
        mock_activity,
        mock_transcribe,
        mock_available,
        preparer,
        tmp_path,
    ):
        (tmp_path / "Artist - Title---abcdefghijk.vocals.m4a").write_bytes(b"aac")

        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))

        mock_transcribe.assert_called_once_with(
            str(tmp_path / "Artist - Title---abcdefghijk.vocals.m4a")
        )
        ass = (tmp_path / "Artist - Title---abcdefghijk.ass").read_text()
        # Heard at 31s, swept from just before it like hand-timed lyrics.
        assert "{\\kf" in ass and ",0:00:27.30," in ass
        assert APPROXIMATE_TIMING_NOTE in ass

    @patch("pikaraoke.lib.song_preparer.is_transcription_available", return_value=True)
    @patch("pikaraoke.lib.song_preparer.transcribe_words")
    @patch(
        "pikaraoke.lib.song_preparer.search_lyrics",
        return_value=[{"trackName": "Title", "plainLyrics": "hello"}],
    )
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=260.0)
    def test_untimed_lyrics_wait_for_a_separated_vocal(
        self, mock_duration, mock_search, mock_transcribe, mock_available, preparer, tmp_path
    ):
        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))
        mock_transcribe.assert_not_called()
        assert not (tmp_path / "Artist - Title---abcdefghijk.ass").exists()

    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_a_mixed_script_title_is_searched_again_by_its_chinese_title(
        self, mock_duration, preparer, tmp_path
    ):
        song = tmp_path / "Matt Lv 吕彦良 '夏夜晴朗的晚上 A Clear Summer Night'---abcdefghijk.mp4"
        with patch(
            "pikaraoke.lib.song_preparer.search_lyrics", side_effect=[[], self.RESULTS]
        ) as mock_search:
            preparer._add_lyrics(str(song))
        assert mock_search.call_args.args == ("夏夜晴朗的晚上",)
        assert song.with_suffix(".ass").exists()

    @patch("pikaraoke.lib.song_preparer.search_lyrics")
    def test_skips_videos_that_show_their_own_lyrics(self, mock_fetch, preparer, tmp_path):
        preparer._add_lyrics(str(tmp_path / "Artist - Title (Karaoke Version)---abcdefghijk.mp4"))
        preparer._add_lyrics(str(tmp_path / "歌手 - 歌名 KTV 伴奏---abcdefghijk.mp4"))
        mock_fetch.assert_not_called()

    @patch("pikaraoke.lib.song_preparer.search_lyrics")
    def test_never_replaces_an_existing_subtitle(self, mock_fetch, preparer, tmp_path):
        (tmp_path / "Artist - Title---abcdefghijk.ass").write_text("handmade")
        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))
        mock_fetch.assert_not_called()

    @patch("pikaraoke.lib.song_preparer.search_lyrics")
    def test_skips_cdg_songs(self, mock_fetch, preparer, tmp_path):
        preparer._add_lyrics(str(tmp_path / "Artist - Title.mp3"))
        mock_fetch.assert_not_called()


class TestAddInstrumental:
    @patch("pikaraoke.lib.song_preparer.separate_vocals", return_value=True)
    def test_announces_a_finished_separation(self, mock_separate, preparer, events, tmp_path):
        ready = []
        events.on("instrumental_ready", ready.append)
        song = str(tmp_path / "Title---abcdefghijk.mp4")

        preparer._add_instrumental(song)

        mock_separate.assert_called_once_with(song, keep_backing=True)
        assert ready == [song]

    @patch("pikaraoke.lib.song_preparer.search_lyrics", return_value=[])
    @patch("pikaraoke.lib.song_preparer.separate_vocals", return_value=True)
    def test_retries_lyrics_once_vocals_exist(
        self, mock_separate, mock_search, preparer, prefs, tmp_path
    ):
        prefs.set("auto_lyrics", True)
        song = str(tmp_path / "Artist - Title---abcdefghijk.mp4")
        preparer._attempted.add(("lyrics", song))

        preparer._add_instrumental(song)

        mock_search.assert_called_once_with("Artist - Title")

    @patch("pikaraoke.lib.song_preparer.separate_vocals")
    def test_skips_a_song_already_separated(self, mock_separate, preparer, tmp_path):
        (tmp_path / "Title---abcdefghijk.instrumental.m4a").write_bytes(b"aac")
        preparer._add_instrumental(str(tmp_path / "Title---abcdefghijk.mp4"))
        mock_separate.assert_not_called()

    @patch("pikaraoke.lib.song_preparer.separate_vocals")
    def test_skips_songs_that_are_already_karaoke_versions(self, mock_separate, preparer):
        preparer._add_instrumental("/songs/Artist - Song (Karaoke Version)---abcdefghijk.mp4")
        preparer._add_instrumental("/songs/楊千嬅 少女的祈禱 原版伴奏---abcdefghijk.mp4")
        mock_separate.assert_not_called()

    @patch("pikaraoke.lib.song_preparer.separate_vocals")
    def test_skips_zipped_cdg(self, mock_separate, preparer):
        preparer._add_instrumental("/songs/Title.zip")
        mock_separate.assert_not_called()


def _write_stems(song_path: str, keep_backing: bool = True) -> bool:
    base = os.path.splitext(song_path)[0]
    for suffix in (".vocals.m4a", ".instrumental.m4a"):
        with open(base + suffix, "wb") as f:
            f.write(b"aac")
    return True


class TestLeadLeftIn:
    @patch("pikaraoke.lib.song_preparer.is_transcription_available", return_value=True)
    @patch("pikaraoke.lib.song_preparer.lead_vocal_left_in", return_value=True)
    @patch("pikaraoke.lib.song_preparer.transcribe_words", return_value=[("要", 1.0)])
    @patch("pikaraoke.lib.song_preparer.separate_vocals")
    def test_a_lead_left_in_the_karaoke_track_takes_all_vocals_out(
        self, mock_separate, mock_transcribe, mock_left_in, mock_available, preparer, tmp_path
    ):
        song = str(tmp_path / "Title---abcdefghijk.mp4")
        mock_separate.side_effect = _write_stems

        preparer._add_instrumental(song)

        assert mock_transcribe.call_args.kwargs == {"in_music": True}
        assert mock_separate.call_args_list[-1] == ((song,), {"keep_backing": False})

    @patch("pikaraoke.lib.song_preparer.is_transcription_available", return_value=True)
    @patch("pikaraoke.lib.song_preparer.lead_vocal_left_in", return_value=False)
    @patch("pikaraoke.lib.song_preparer.transcribe_words", return_value=[])
    @patch("pikaraoke.lib.song_preparer.separate_vocals")
    def test_a_clean_karaoke_track_is_kept(
        self, mock_separate, mock_transcribe, mock_left_in, mock_available, preparer, tmp_path
    ):
        song = str(tmp_path / "Title---abcdefghijk.mp4")
        mock_separate.side_effect = _write_stems

        preparer._add_instrumental(song)

        mock_separate.assert_called_once_with(song, keep_backing=True)

    @patch("pikaraoke.lib.song_preparer.is_transcription_available", return_value=True)
    @patch("pikaraoke.lib.song_preparer.transcribe_words")
    @patch("pikaraoke.lib.song_preparer.separate_vocals")
    def test_without_backing_vocals_all_come_out_with_nothing_to_check(
        self, mock_separate, mock_transcribe, mock_available, preparer, prefs, tmp_path
    ):
        prefs.set("keep_backing_vocals", False)
        song = str(tmp_path / "Title---abcdefghijk.mp4")
        mock_separate.side_effect = _write_stems

        preparer._add_instrumental(song)

        mock_separate.assert_called_once_with(song, keep_backing=False)
        mock_transcribe.assert_not_called()


class TestRemoveAllVocals:
    @patch("pikaraoke.lib.song_preparer.has_all_vocals_removed", return_value=False)
    @patch("pikaraoke.lib.song_preparer.separate_vocals", return_value=True)
    def test_separates_again_without_backing_vocals_once(
        self, mock_separate, mock_removed, preparer, events
    ):
        done = []
        events.on("all_vocals_removed", done.append)

        assert preparer.remove_all_vocals("/songs/a.mp4") is True
        assert preparer.remove_all_vocals("/songs/a.mp4") is False
        assert preparer.all_vocals_removable("/songs/a.mp4") is False
        _run_pending(preparer)

        mock_separate.assert_called_once_with("/songs/a.mp4", keep_backing=False)
        assert done == ["/songs/a.mp4"]

    @patch("pikaraoke.lib.song_preparer.has_all_vocals_removed", return_value=True)
    def test_nothing_to_do_for_a_track_already_without_them(self, mock_removed, preparer):
        assert preparer.remove_all_vocals("/songs/a.mp4") is False
        assert preparer._pending.empty()
