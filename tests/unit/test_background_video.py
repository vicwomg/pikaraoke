"""Tests for the background videos: which ones play, and what serves them."""

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes.splash import splash_bp
from pikaraoke.routes.stream import stream_bp
from tests.conftest import make_route_app

CANARY = "TOP-SECRET-CANARY"


@pytest.fixture
def video_dir(tmp_path):
    """A directory of background videos, with a canary file one level up."""
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "one.mp4").write_text("first-video", encoding="utf-8")
    (videos / "two.webm").write_text("second-video", encoding="utf-8")
    (videos / "notes.txt").write_text("not a video", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(CANARY, encoding="utf-8")
    return videos


class TestServingOneVideo:
    """The route named in the page, serving a video the URL asks for by name."""

    @pytest.fixture
    def app(self):
        return make_route_app(stream_bp, [])

    @pytest.fixture
    def karaoke(self):
        k = MagicMock()
        with patch("pikaraoke.routes.stream.get_karaoke_instance", return_value=k):
            yield k

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
    def test_refuses_a_path_outside_the_video_directory(self, client, karaoke, video_dir, attempt):
        """Flask's converter refuses a forward slash, but not a backslash or a
        drive letter, so on Windows these all resolved outside the directory."""
        karaoke.bg_video_path = str(video_dir)

        response = client.get(f"/stream/bg_video/{attempt}")

        assert response.status_code == 404
        assert CANARY not in response.get_data(as_text=True)

    def test_serves_a_video_from_the_directory(self, client, karaoke, video_dir):
        karaoke.bg_video_path = str(video_dir)

        response = client.get("/stream/bg_video/one.mp4")

        assert response.status_code == 200
        assert response.get_data(as_text=True) == "first-video"

    @pytest.mark.parametrize(
        ("video", "expected_type"),
        [("one.mp4", "video/mp4"), ("two.webm", "video/webm")],
    )
    def test_a_video_declares_its_own_type(self, client, karaoke, video_dir, video, expected_type):
        """A directory may hold mixed formats, and Safari believes the header."""
        karaoke.bg_video_path = str(video_dir)

        assert client.get(f"/stream/bg_video/{video}").mimetype == expected_type

    def test_a_neighbour_is_not_served_when_the_path_names_one_video(
        self, client, karaoke, video_dir
    ):
        """Naming one video is not consent to share the rest of its folder."""
        karaoke.bg_video_path = str(video_dir / "one.mp4")

        assert client.get("/stream/bg_video/two.webm").status_code == 404
        assert client.get("/stream/bg_video/one.mp4").status_code == 200

    @pytest.mark.parametrize(
        "make_path",
        [
            pytest.param(lambda video_dir: None, id="unset"),
            pytest.param(lambda video_dir: str(video_dir / "gone"), id="missing"),
        ],
    )
    def test_a_path_with_no_video_is_404(self, client, karaoke, video_dir, make_path):
        karaoke.bg_video_path = make_path(video_dir)

        assert client.get("/stream/bg_video/one.mp4").status_code == 404


def _rendered_playlist(client):
    """The video URLs the splash page hands its player, in the order they play."""
    page = client.get("/splash").get_data(as_text=True)
    return json.loads(re.search(r"bgVideoPlaylist: (\[.*?\]),", page).group(1))


class TestChoosingTheVideos:
    """The order is settled once, as the page renders."""

    @pytest.fixture
    def app(self):
        return make_route_app(
            splash_bp,
            [
                ("/stream/bg_video/<file>", "stream.stream_bg_video"),
                ("/logo", "images.logo"),
                ("/qrcode", "images.qrcode"),
            ],
        )

    @pytest.fixture
    def karaoke(self):
        k = MagicMock()
        k.is_raspberry_pi = False
        with (
            patch("pikaraoke.routes.splash.get_karaoke_instance", return_value=k),
            patch("pikaraoke.routes.splash.get_site_name", return_value="PiKaraoke"),
        ):
            yield k

    def test_plays_every_video_the_directory_holds_and_nothing_else(
        self, client, karaoke, video_dir
    ):
        karaoke.bg_video_path = str(video_dir)

        assert sorted(_rendered_playlist(client)) == [
            "/stream/bg_video/one.mp4",
            "/stream/bg_video/two.webm",
        ]

    def test_eventually_renders_a_different_order(self, client, karaoke, video_dir):
        """Shuffled, not "always the same first" -- two videos, so twenty renders
        that all agree would be a one-in-a-million coincidence."""
        karaoke.bg_video_path = str(video_dir)

        assert len({tuple(_rendered_playlist(client)) for _ in range(20)}) == 2

    def test_a_path_naming_one_video_plays_that_video_alone(self, client, karaoke, video_dir):
        """The --dolphly path, and the behaviour that existed before directories."""
        karaoke.bg_video_path = str(video_dir / "one.mp4")

        assert _rendered_playlist(client) == ["/stream/bg_video/one.mp4"]

    @pytest.mark.parametrize(
        "make_path",
        [
            pytest.param(lambda tmp_path: None, id="unset"),
            pytest.param(lambda tmp_path: str(tmp_path / "gone"), id="missing"),
            pytest.param(lambda tmp_path: str(tmp_path), id="no-videos-in-it"),
        ],
    )
    def test_a_path_with_no_video_leaves_the_page_without_one(
        self, client, karaoke, tmp_path, make_path
    ):
        """Nothing to play beats requesting a video that 404s."""
        (tmp_path / "notes.txt").write_text("not a video", encoding="utf-8")
        karaoke.bg_video_path = make_path(tmp_path)

        assert _rendered_playlist(client) == []
