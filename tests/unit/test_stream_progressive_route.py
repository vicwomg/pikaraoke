"""Tests for the progressive MP4 stream route."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes.stream import stream_bp
from tests.conftest import make_route_app


@pytest.fixture
def app():
    return make_route_app(stream_bp, [("/", "home.home")])


def test_finishes_when_ffmpeg_process_is_none(client, tmp_path):
    """A None process (transcode finished or never started) flushes the file.

    Reading None.poll() used to raise AttributeError mid-stream.
    """
    content = b"fake mp4 bytes"
    (tmp_path / "song.mp4").write_bytes(content)

    k = MagicMock()
    k.playback_controller.ffmpeg_process = None

    with (
        patch("pikaraoke.routes.stream.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.stream.get_tmp_dir", return_value=str(tmp_path)),
    ):
        response = client.get("/stream/song.mp4")
        body = response.data  # forces the streaming generator to run to completion

    assert response.status_code == 200
    assert body == content
    k.playback_controller.start_song.assert_called_once_with("song")
