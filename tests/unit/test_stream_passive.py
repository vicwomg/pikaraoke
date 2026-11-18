"""The ?passive=1 flag lets a phone fetch a stream without starting the song.

A watch-along phone fetches the same stream as the TV. Without the flag, loading
it would trip start_song() - skewing position tracking and the master election -
just because the phone loaded faster than the splash.
"""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes.stream import stream_bp
from tests.conftest import make_route_app


@pytest.fixture
def app():
    return make_route_app(stream_bp, [("/", "home.home")])


def _karaoke():
    k = MagicMock()
    # A None process makes the progressive route flush and finish at once.
    k.playback_controller.ffmpeg_process = None
    return k


# (url, the id start_song is called with) for each route that starts a song.
ROUTES = [
    ("/stream/song.m3u8", "song"),
    ("/stream/song.mp4", "song"),
    ("/stream/full/song", "song"),
]


@pytest.mark.parametrize("url, song_id", ROUTES)
def test_plain_fetch_starts_the_song(client, tmp_path, url, song_id):
    (tmp_path / "song.m3u8").write_text("#EXTM3U")
    (tmp_path / "song.mp4").write_bytes(b"fake mp4 bytes")
    k = _karaoke()

    with (
        patch("pikaraoke.routes.stream.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.stream.get_tmp_dir", return_value=str(tmp_path)),
    ):
        response = client.get(url)
        response.data  # drain any streaming generator

    assert response.status_code == 200
    k.playback_controller.start_song.assert_called_once_with(song_id)


@pytest.mark.parametrize("url, song_id", ROUTES)
def test_passive_fetch_does_not_start_the_song(client, tmp_path, url, song_id):
    (tmp_path / "song.m3u8").write_text("#EXTM3U")
    (tmp_path / "song.mp4").write_bytes(b"fake mp4 bytes")
    k = _karaoke()

    with (
        patch("pikaraoke.routes.stream.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.stream.get_tmp_dir", return_value=str(tmp_path)),
    ):
        response = client.get(url + "?passive=1")
        response.data

    assert response.status_code == 200
    k.playback_controller.start_song.assert_not_called()
