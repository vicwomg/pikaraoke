"""Unit tests for background song preparation."""

import os
from unittest.mock import patch

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.song_preparer import SongPreparer

LRC = "[00:01.00]hello"


def _name(path: str, remove_youtube_id: bool = False) -> str:
    return os.path.basename(path).rsplit("---", 1)[0]


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

    @patch("pikaraoke.lib.song_preparer.fetch_synced_lyrics", side_effect=RuntimeError("boom"))
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_one_failing_song_does_not_stop_the_next(
        self, mock_duration, mock_fetch, preparer, prefs, tmp_path
    ):
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

    @patch("pikaraoke.lib.song_preparer.fetch_synced_lyrics", return_value=None)
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_a_lyrics_miss_is_not_looked_up_again(
        self, mock_duration, mock_fetch, preparer, prefs, tmp_path
    ):
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


class TestAddLyrics:
    @patch("pikaraoke.lib.song_preparer.fetch_synced_lyrics", return_value=LRC)
    @patch("pikaraoke.lib.song_preparer.get_media_duration", return_value=200.0)
    def test_writes_subtitle_and_announces_it(
        self, mock_duration, mock_fetch, preparer, events, tmp_path
    ):
        song = tmp_path / "Artist - Title (Official Video)---abcdefghijk.mp4"
        song.write_bytes(b"video")
        ready = []
        events.on("lyrics_ready", ready.append)

        preparer._add_lyrics(str(song))

        mock_fetch.assert_called_once_with("Artist - Title", 200.0)
        assert (
            "{\\kf" in (tmp_path / "Artist - Title (Official Video)---abcdefghijk.ass").read_text()
        )
        assert ready == [str(song)]

    @patch("pikaraoke.lib.song_preparer.fetch_synced_lyrics")
    def test_skips_videos_that_show_their_own_lyrics(self, mock_fetch, preparer, tmp_path):
        preparer._add_lyrics(str(tmp_path / "Artist - Title (Karaoke Version)---abcdefghijk.mp4"))
        preparer._add_lyrics(str(tmp_path / "歌手 - 歌名 KTV 伴奏---abcdefghijk.mp4"))
        mock_fetch.assert_not_called()

    @patch("pikaraoke.lib.song_preparer.fetch_synced_lyrics")
    def test_never_replaces_an_existing_subtitle(self, mock_fetch, preparer, tmp_path):
        (tmp_path / "Artist - Title---abcdefghijk.ass").write_text("handmade")
        preparer._add_lyrics(str(tmp_path / "Artist - Title---abcdefghijk.mp4"))
        mock_fetch.assert_not_called()

    @patch("pikaraoke.lib.song_preparer.fetch_synced_lyrics")
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

        mock_separate.assert_called_once_with(song)
        assert ready == [song]

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
