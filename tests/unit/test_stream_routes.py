"""Route-level tests for the stem audio endpoint.

Focused on the branching between raw send_file and the transformed-pipe
path added alongside vocal_removal + pitch/normalize support.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.lib.stream_manager import ActiveStems
from pikaraoke.routes.stream import stream_bp


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.register_blueprint(stream_bp)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_stems(path: str, *, semitones: int = 0, normalize: bool = False) -> ActiveStems:
    done = threading.Event()
    done.set()
    ready = threading.Event()
    ready.set()
    return ActiveStems(
        vocals_path=path,
        instrumental_path=path,
        format="wav",
        done_event=done,
        ready_event=ready,
        processed_seconds=180.0,
        total_seconds=180.0,
        semitones=semitones,
        normalize=normalize,
    )


class TestStreamStemAudioPipe:
    """Verifies the transforms-active branch of the stem route."""

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_pipes_when_transforms_active(self, mock_get_instance, client, tmp_path):
        stem_file = tmp_path / "vocals.wav"
        stem_file.write_bytes(b"\x00" * 1024)
        stems = _make_stems(str(stem_file), semitones=2, normalize=True)

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"abc": stems}
        mock_get_instance.return_value = mock_karaoke

        fake_gen = lambda: iter([b"WAVE"])
        with patch("pikaraoke.lib.audio_processor.stream_wav_range") as mock_stream:
            mock_stream.return_value = (
                fake_gen,
                200,
                {"Content-Type": "audio/wav", "Content-Length": "4"},
                4,
            )
            response = client.get("/stream/abc/vocals.wav")

        assert response.status_code == 200
        mock_stream.assert_called_once()
        config = mock_stream.call_args.args[0]
        assert config.source_path == str(stem_file)
        assert config.semitones == 2
        assert config.normalize is True
        assert config.duration_sec == 180.0

    @patch("pikaraoke.routes.stream.send_file")
    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_raw_when_no_transforms(self, mock_get_instance, mock_send_file, client, tmp_path):
        stem_file = tmp_path / "vocals.wav"
        stem_file.write_bytes(b"\x00" * 16)
        stems = _make_stems(str(stem_file))

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"abc": stems}
        mock_get_instance.return_value = mock_karaoke

        mock_send_file.return_value = ("OK", 200)

        with patch("pikaraoke.lib.audio_processor.stream_wav_range") as mock_stream:
            client.get("/stream/abc/vocals.wav")

        mock_stream.assert_not_called()
        mock_send_file.assert_called_once()

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_raw_tail_when_demucs_not_done(self, mock_get_instance, client, tmp_path):
        """Transforms set but Demucs still writing: skip pipe, fall to tail reader."""
        stem_file = tmp_path / "vocals.wav"
        stem_file.write_bytes(b"header")
        stems = _make_stems(str(stem_file), semitones=2)
        stems.done_event.clear()

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"abc": stems}
        mock_get_instance.return_value = mock_karaoke

        with patch("pikaraoke.lib.audio_processor.stream_wav_range") as mock_stream:
            # set done_event shortly after so the tail generator exits
            def unblock():
                import time

                time.sleep(0.1)
                stems.done_event.set()

            threading.Thread(target=unblock, daemon=True).start()
            response = client.get("/stream/abc/vocals.wav")

        mock_stream.assert_not_called()
        # tail-stream branch returns a streaming response with Accept-Ranges: none
        assert response.headers.get("Accept-Ranges") == "none"
