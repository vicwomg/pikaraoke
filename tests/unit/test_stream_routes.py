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
        assert mock_send_file.call_args.args[0] == str(stem_file)

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
        assert response.status_code == 200
        assert response.headers.get("Accept-Ranges") == "none"


def _mp3_stems(path: str) -> ActiveStems:
    done = threading.Event()
    done.set()
    ready = threading.Event()
    ready.set()
    return ActiveStems(
        vocals_path=path,
        instrumental_path=path,
        format="mp3",
        done_event=done,
        ready_event=ready,
        processed_seconds=180.0,
        total_seconds=180.0,
    )


class TestStreamStemAudioMp3Replay:
    """After first-play WAV cleanup only MP3 stems remain on disk. Replay
    must serve them correctly: right mimetype, Range-aware, no 416.
    """

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_serves_mp3_with_audio_mpeg_mimetype(self, mock_get_instance, client, tmp_path):
        stem_file = tmp_path / "vocals.mp3"
        stem_file.write_bytes(b"\xff\xfb" + b"\x00" * 2048)  # fake MP3 header + data
        stems = _mp3_stems(str(stem_file))

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"uid2": stems}
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/stream/uid2/vocals.mp3")

        assert response.status_code == 200
        assert response.mimetype == "audio/mpeg"
        assert response.headers.get("Accept-Ranges") == "bytes"

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_range_request_on_mp3_returns_206(self, mock_get_instance, client, tmp_path):
        stem_file = tmp_path / "vocals.mp3"
        stem_file.write_bytes(b"\xff\xfb" + b"\x00" * 2048)
        stems = _mp3_stems(str(stem_file))

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"uid2": stems}
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/stream/uid2/vocals.mp3", headers={"Range": "bytes=100-200"})

        assert response.status_code == 206
        assert response.mimetype == "audio/mpeg"
        content_range = response.headers.get("Content-Range", "")
        assert content_range.startswith("bytes 100-200/")

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_range_beyond_mp3_returns_416_not_silent_200(self, mock_get_instance, client, tmp_path):
        """If a client requests a Range past the MP3 file length, the server
        must return 416 (so the audio element fails fast) rather than a
        truncated 206 that would desync playback.
        """
        stem_file = tmp_path / "vocals.mp3"
        stem_file.write_bytes(b"\xff\xfb" + b"\x00" * 64)  # tiny file
        stems = _mp3_stems(str(stem_file))

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"uid2": stems}
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/stream/uid2/vocals.mp3", headers={"Range": "bytes=100000-200000"})

        assert response.status_code == 416


class TestStreamStemAudioWavUrlWithMp3OnDisk:
    """Regression guard for the cross-format fallback: when the URL says
    .wav but only the .mp3 sibling is on disk (and ActiveStems still points
    at the now-gone .wav), the route must swap to .mp3 and serve it — but
    it must NOT answer an out-of-bounds Range with 416 because the browser
    is pinned to the old WAV Content-Length. Instead it should shape the
    response so the audio element can recover (serve 200 full or 206
    against the MP3 size).
    """

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_fallback_serves_mp3_bytes_for_wav_url(self, mock_get_instance, client, tmp_path):
        # Simulate the mid-session race: ActiveStems still has .wav but only
        # .mp3 exists on disk (e.g. if cleanup ran early).
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        mp3_file = cache_dir / "vocals.mp3"
        mp3_file.write_bytes(b"\xff\xfb" + b"\x00" * 2048)
        wav_path = str(cache_dir / "vocals.wav")  # does not exist on disk

        done = threading.Event()
        done.set()
        ready = threading.Event()
        ready.set()
        stems = ActiveStems(
            vocals_path=wav_path,
            instrumental_path=wav_path,
            format="wav",
            done_event=done,
            ready_event=ready,
            processed_seconds=180.0,
            total_seconds=180.0,
        )
        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"uid": stems}
        mock_get_instance.return_value = mock_karaoke

        response = client.get("/stream/uid/vocals.wav")

        assert response.status_code == 200
        assert response.mimetype == "audio/mpeg"
        # ActiveStems must be updated so subsequent fetches skip the fallback.
        assert stems.vocals_path == str(mp3_file)
        assert stems.format == "mp3"

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_range_request_beyond_mp3_when_wav_url_used(self, mock_get_instance, client, tmp_path):
        """This is the exact 416 the original bug report surfaced. Once
        ActiveStems.vocals_path is swapped to the smaller MP3, a browser
        Range header pinned to the old WAV size still produces 416. The
        test documents the current behaviour so the higher-level fix (not
        deleting WAVs mid-session) can be verified end-to-end.
        """
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        mp3_file = cache_dir / "vocals.mp3"
        mp3_file.write_bytes(b"\xff\xfb" + b"\x00" * 64)  # tiny
        wav_path = str(cache_dir / "vocals.wav")

        done = threading.Event()
        done.set()
        ready = threading.Event()
        ready.set()
        stems = ActiveStems(
            vocals_path=wav_path,
            instrumental_path=wav_path,
            format="wav",
            done_event=done,
            ready_event=ready,
            processed_seconds=180.0,
            total_seconds=180.0,
        )
        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.stream_manager.active_stems = {"uid": stems}
        mock_get_instance.return_value = mock_karaoke

        # Browser thinks the file is WAV-sized (megabytes) and asks for bytes
        # past the MP3 length.
        response = client.get("/stream/uid/vocals.wav", headers={"Range": "bytes=100000-200000"})

        # Documents the current behavior — the true fix is keeping WAVs on
        # disk for the active song's lifetime (see test_demucs_processor).
        assert response.status_code == 416


class TestSubtitleOverride:
    """``/subtitle/<id>`` honours the per-song source pin from the picker."""

    def _setup(self, mock_get_instance, tmp_path, *, override=None, variant_exists=False):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        canonical = tmp_path / "Foo---abc.ass"
        canonical.write_text("CANONICAL")
        if variant_exists:
            (tmp_path / "Foo---abc.lrclib.ass").write_text("VARIANT_LRCLIB")

        mock_karaoke = MagicMock()
        mock_karaoke.playback_controller.now_playing_filename = str(song_path)
        mock_karaoke.playback_controller.now_playing_url = "/stream/uid"
        mock_karaoke.db.get_song_id_by_path.return_value = 42
        mock_karaoke.db.get_subtitle_source_override.return_value = override
        # Default: no fetch in flight. Tests that need the in-flight branch
        # override this explicitly. Without this, the MagicMock auto-attr
        # returns a truthy MagicMock and the stale-clear branch is skipped.
        mock_karaoke.lyrics_service.is_fetch_in_flight.return_value = False
        mock_get_instance.return_value = mock_karaoke
        return mock_karaoke, song_path

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_serves_canonical_without_override(self, mock_get, client, tmp_path):
        self._setup(mock_get, tmp_path, override=None)
        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        assert r.data == b"CANONICAL"

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_serves_variant_when_override_pinned(self, mock_get, client, tmp_path):
        self._setup(mock_get, tmp_path, override="lrclib", variant_exists=True)
        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        assert r.data == b"VARIANT_LRCLIB"

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_stale_override_falls_back_and_clears(self, mock_get, client, tmp_path):
        k, _ = self._setup(mock_get, tmp_path, override="genius-sync", variant_exists=False)
        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        # Falls back to canonical when the variant file is missing.
        assert r.data == b"CANONICAL"
        # And clears the stale pin so the picker shows ``download`` again.
        k.db.set_subtitle_source_override.assert_called_once_with(42, None)

    @patch("pikaraoke.routes.stream.get_karaoke_instance")
    def test_off_override_serves_canonical_without_clearing(self, mock_get, client, tmp_path):
        k, _ = self._setup(mock_get, tmp_path, override="off")
        r = client.get("/subtitle/uid")
        assert r.status_code == 200
        # ``off`` is the picker's hide toggle; the splash skips Octopus init
        # via subtitle_disabled, but the canonical URL is still served so
        # other clients (admin browsers) get a body.
        assert r.data == b"CANONICAL"
        # ``off`` is not stale — no clear.
        k.db.set_subtitle_source_override.assert_not_called()
