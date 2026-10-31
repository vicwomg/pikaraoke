"""Tests for the upload page and endpoint: it brings local files into the library."""

import io
import json
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.routes.upload import upload_bp
from tests.conftest import make_route_app


@pytest.fixture
def app():
    return make_route_app(upload_bp, [])


def _instance(tmp_path):
    k = MagicMock()
    k.download_path = str(tmp_path)
    return k


def _post(client, k, files):
    with patch("pikaraoke.routes.upload.get_karaoke_instance", return_value=k):
        return client.post(
            "/api/upload",
            data={"files": files},
            content_type="multipart/form-data",
        )


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    buffer.seek(0)
    return buffer


class TestUploadPage:
    def test_renders_for_admin(self, client):
        with patch("pikaraoke.routes.upload.get_site_name", return_value="PiKaraoke"):
            response = client.get("/upload")
        assert response.status_code == 200
        assert b"drop-zone" in response.data


class TestUpload:
    def test_saves_registers_and_announces(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"video"), "My Song.mp4"))
        assert response.status_code == 200
        assert json.loads(response.data)["results"] == [{"name": "My Song.mp4", "ok": True}]
        target = os.path.join(str(tmp_path), "My Song.mp4")
        assert os.path.isfile(target)
        k.song_manager.register_song.assert_called_once_with(target)  # pylint: disable=no-member
        k.socketio.emit.assert_called_once()

    def test_rejects_unsupported_extension(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"nope"), "notes.txt"))
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []
        k.song_manager.register_song.assert_not_called()  # pylint: disable=no-member

    def test_collision_gets_a_suffix(self, tmp_path, client):
        (tmp_path / "Song.mp4").write_text("first")
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"second"), "Song.mp4"))
        assert json.loads(response.data)["results"][0]["ok"] is True
        target = os.path.join(str(tmp_path), "Song (2).mp4")
        assert os.path.isfile(target)
        k.song_manager.register_song.assert_called_once_with(target)  # pylint: disable=no-member

    def test_a_name_that_climbs_out_is_confined(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"x"), "../../evil.mp4"))
        assert json.loads(response.data)["results"][0]["ok"] is True
        registered = k.song_manager.register_song.call_args[0][0]  # pylint: disable=no-member
        assert os.path.dirname(registered) == str(tmp_path)

    def test_each_file_gets_its_own_result(self, tmp_path, client):
        k = _instance(tmp_path)
        files = [(io.BytesIO(b"a"), "One.mp4"), (io.BytesIO(b"b"), "bad.txt")]
        response = _post(client, k, files)
        results = json.loads(response.data)["results"]
        assert [r["ok"] for r in results] == [True, False]

    def test_no_files_is_a_400(self, tmp_path, client):
        k = _instance(tmp_path)
        with patch("pikaraoke.routes.upload.get_karaoke_instance", return_value=k):
            response = client.post("/api/upload", data={}, content_type="multipart/form-data")
        assert response.status_code == 400


class TestPairs:
    def test_mp3_with_cdg_is_one_song(self, tmp_path, client):
        k = _instance(tmp_path)
        files = [(io.BytesIO(b"audio"), "Song.mp3"), (io.BytesIO(b"graphics"), "Song.cdg")]
        response = _post(client, k, files)
        assert json.loads(response.data)["results"] == [{"name": "Song.mp3", "ok": True}]
        mp3 = os.path.join(str(tmp_path), "Song.mp3")
        assert os.path.isfile(mp3)
        assert os.path.isfile(os.path.join(str(tmp_path), "Song.cdg"))
        k.song_manager.register_song.assert_called_once_with(mp3)  # pylint: disable=no-member

    def test_video_with_subtitle_is_one_song(self, tmp_path, client):
        k = _instance(tmp_path)
        files = [(io.BytesIO(b"video"), "Song.mp4"), (io.BytesIO(b"subs"), "Song.ass")]
        response = _post(client, k, files)
        assert json.loads(response.data)["results"][0]["ok"] is True
        mp4 = os.path.join(str(tmp_path), "Song.mp4")
        assert os.path.isfile(os.path.join(str(tmp_path), "Song.ass"))
        k.song_manager.register_song.assert_called_once_with(mp4)  # pylint: disable=no-member

    def test_companion_inherits_the_collision_suffix(self, tmp_path, client):
        (tmp_path / "Song.mp3").write_text("first")
        k = _instance(tmp_path)
        files = [(io.BytesIO(b"audio"), "Song.mp3"), (io.BytesIO(b"graphics"), "Song.cdg")]
        response = _post(client, k, files)
        assert json.loads(response.data)["results"][0]["ok"] is True
        # The pair moves together so the player's stem-match still finds the .cdg.
        assert os.path.isfile(os.path.join(str(tmp_path), "Song (2).mp3"))
        assert os.path.isfile(os.path.join(str(tmp_path), "Song (2).cdg"))

    def test_lone_mp3_is_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"audio"), "Song.mp3"))
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []
        k.song_manager.register_song.assert_not_called()  # pylint: disable=no-member

    def test_lone_cdg_is_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"graphics"), "Song.cdg"))
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []

    def test_lone_subtitle_is_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"subs"), "Song.ass"))
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []

    def test_cdg_beside_a_video_is_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        files = [(io.BytesIO(b"video"), "Song.mp4"), (io.BytesIO(b"graphics"), "Song.cdg")]
        response = _post(client, k, files)
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []
        k.song_manager.register_song.assert_not_called()  # pylint: disable=no-member


class TestZipValidation:
    def test_matching_cdg_pair_is_accepted(self, tmp_path, client):
        k = _instance(tmp_path)
        archive = _zip_bytes([("Song.mp3", b"audio"), ("Song.cdg", b"graphics")])
        response = _post(client, k, (archive, "Song.zip"))
        assert json.loads(response.data)["results"][0]["ok"] is True
        assert os.path.isfile(os.path.join(str(tmp_path), "Song.zip"))

    def test_video_plus_subtitle_zip_is_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        archive = _zip_bytes([("Song.mp4", b"video"), ("Song.ass", b"subs")])
        response = _post(client, k, (archive, "Song.zip"))
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []
        k.song_manager.register_song.assert_not_called()  # pylint: disable=no-member

    def test_mismatched_stems_are_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        archive = _zip_bytes([("Audio.mp3", b"a"), ("Graphics.cdg", b"g")])
        response = _post(client, k, (archive, "Song.zip"))
        assert json.loads(response.data)["results"][0]["ok"] is False

    def test_corrupt_zip_is_rejected(self, tmp_path, client):
        k = _instance(tmp_path)
        response = _post(client, k, (io.BytesIO(b"not a zip at all"), "Song.zip"))
        assert json.loads(response.data)["results"][0]["ok"] is False

    def test_a_bomb_is_rejected_by_unpacked_size(self, tmp_path, client):
        k = _instance(tmp_path)
        archive = _zip_bytes([("Song.mp3", b"x" * 1000), ("Song.cdg", b"y" * 1000)])
        with (
            patch("pikaraoke.routes.upload.get_karaoke_instance", return_value=k),
            patch("pikaraoke.routes.upload._MAX_ZIP_UNPACKED_BYTES", 100),
        ):
            response = client.post(
                "/api/upload",
                data={"files": (archive, "Song.zip")},
                content_type="multipart/form-data",
            )
        assert json.loads(response.data)["results"][0]["ok"] is False
        assert os.listdir(str(tmp_path)) == []


class TestAdminGate:
    def test_page_refused_for_non_admin(self):
        response = make_route_app(upload_bp, [], admin=False).test_client().get("/upload")
        assert response.status_code in (302, 303)

    def test_api_refused_for_non_admin(self):
        response = (
            make_route_app(upload_bp, [], admin=False)
            .test_client()
            .post("/api/upload", data={}, content_type="multipart/form-data")
        )
        assert response.status_code == 403
