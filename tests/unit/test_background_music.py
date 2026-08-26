"""Tests for the background music routes, focused on the path the URL supplies."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes.background_music import background_music_bp
from tests.conftest import make_route_app

CANARY = "TOP-SECRET-CANARY"


@pytest.fixture
def app():
    return make_route_app(background_music_bp, [])


@pytest.fixture
def music_dir(tmp_path):
    """A music directory with a song in it, and a canary file one level up."""
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.mp3").write_text("legit-audio", encoding="utf-8")
    (music / "song.mp4").write_text("legit-video", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(CANARY, encoding="utf-8")

    k = MagicMock()
    k.bg_music_path = str(music)
    with patch("pikaraoke.routes.background_music.get_karaoke_instance", return_value=k):
        yield tmp_path


class TestBackgroundMusicPath:
    """Flask's converter refuses a forward slash, but not a backslash or a drive
    letter, so on Windows these all resolved outside the music directory."""

    @pytest.mark.parametrize(
        "attempt",
        [
            "..%5Csecret.txt",
            "..\\secret.txt",
            "..%2Fsecret.txt",
            "..%5C..%5Csecret.txt",
            "C%3A%5CWindows%5Cwin.ini",
        ],
    )
    def test_refuses_a_path_outside_the_music_directory(self, client, music_dir, attempt):
        response = client.get(f"/bg_music/{attempt}")

        assert response.status_code == 404
        assert CANARY not in response.get_data(as_text=True)

    def test_serves_a_file_in_the_music_directory(self, client, music_dir):
        response = client.get("/bg_music/song.mp3")

        assert response.status_code == 200
        assert response.get_data(as_text=True) == "legit-audio"

    def test_unknown_song_is_404(self, client, music_dir):
        assert client.get("/bg_music/nothing-here.mp3").status_code == 404

    @pytest.mark.parametrize(
        ("track", "expected_type"),
        [("song.mp3", "audio/mpeg"), ("song.mp4", "video/mp4")],
    )
    def test_a_track_declares_its_own_type(self, client, music_dir, track, expected_type):
        """The playlist accepts mp4 as well as mp3, and Safari believes the header."""
        assert client.get(f"/bg_music/{track}").mimetype == expected_type
