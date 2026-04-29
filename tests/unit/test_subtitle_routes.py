"""Route tests for the subtitle-source picker.

Covers admin gating, source whitelist (CG2), available/download/off
dispatch paths, N/A errors, and the user-source guard.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.subtitle import subtitle_bp


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.register_blueprint(subtitle_bp)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _post_json(client, body, *, admin=True):
    headers = {"Content-Type": "application/json"}
    if admin:
        client.set_cookie("admin", "secret")
    return client.post("/subtitle_source", data=json.dumps(body), headers=headers)


@pytest.fixture
def mocks():
    """Patch is_admin (off by default), get_karaoke_instance, broadcast_event."""
    with (
        patch("pikaraoke.routes.subtitle.is_admin", return_value=True) as mock_admin,
        patch("pikaraoke.routes.subtitle.get_karaoke_instance") as mock_get,
        patch("pikaraoke.routes.subtitle.broadcast_event") as mock_emit,
    ):
        k = MagicMock()
        k.db = MagicMock()
        k.db.get_subtitle_source_override.return_value = None
        k.lyrics_service = MagicMock()
        mock_get.return_value = k
        yield {"admin": mock_admin, "k": k, "emit": mock_emit}


class TestAdminGate:
    def test_non_admin_returns_403(self, client, mocks):
        mocks["admin"].return_value = False
        r = _post_json(client, {"song_id": 1, "source": "lrclib"}, admin=False)
        assert r.status_code == 403


class TestWhitelist:
    """CG2 — source must be in VALID_SUBTITLE_SOURCES BEFORE any DB work."""

    def test_unknown_source_returns_400_without_db(self, client, mocks):
        r = _post_json(client, {"song_id": 1, "source": "../etc/passwd"})
        assert r.status_code == 400
        # DB never consulted for a hostile source — this is the security
        # property: row existence cannot be probed via the source field.
        mocks["k"].db.get_song_by_id.assert_not_called()

    def test_missing_source_returns_400(self, client, mocks):
        r = _post_json(client, {"song_id": 1})
        assert r.status_code == 400
        mocks["k"].db.get_song_by_id.assert_not_called()

    def test_non_int_song_id_returns_400(self, client, mocks):
        r = _post_json(client, {"song_id": "abc", "source": "lrclib"})
        assert r.status_code == 400


class TestAvailable:
    def test_existing_variant_returns_ok(self, client, mocks, tmp_path):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        variant = tmp_path / "Foo---abc.lrclib.ass"
        variant.write_text("[Script Info]\n")
        row = {"file_path": str(song_path), "lyrics_provenance": "auto_line"}
        mocks["k"].db.get_song_by_id.return_value = row

        r = _post_json(client, {"song_id": 1, "source": "lrclib"})

        assert r.status_code == 200
        assert json.loads(r.data) == {"status": "ok"}
        mocks["k"].db.set_subtitle_source_override.assert_called_once_with(1, "lrclib")
        # No fetch dispatch when the variant is already on disk.
        mocks["k"].lyrics_service.fetch_variant.assert_not_called()


class TestDownload:
    def test_missing_variant_dispatches_fetch_and_returns_pending(self, client, mocks, tmp_path):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        row = {"file_path": str(song_path), "lyrics_provenance": "auto_line"}
        mocks["k"].db.get_song_by_id.return_value = row
        mocks["k"].lyrics_service.dispatch_variant_fetch.return_value = True

        r = _post_json(client, {"song_id": 1, "source": "AI"})

        assert r.status_code == 202
        assert json.loads(r.data) == {"status": "pending"}
        mocks["k"].db.set_subtitle_source_override.assert_called_once_with(1, "AI")
        # Dispatch is synchronous (it claims the in-flight slot) so we can
        # assert on the call without polling. The race-fix contract: the
        # claim must happen BEFORE lyrics_upgraded is emitted, so the
        # splash GET cannot race past the worker thread.
        mocks["k"].lyrics_service.dispatch_variant_fetch.assert_called_once_with(
            str(song_path), "AI"
        )

    def test_dispatch_happens_before_lyrics_upgraded_emit(self, client, mocks, tmp_path):
        """Race-fix contract: in-flight slot must be claimed BEFORE the
        cache-bust event fires, otherwise the splash's GET /subtitle/<id>
        races past the worker's own ``add(key)`` and the stream route
        clears the just-set pin.
        """
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        row = {"file_path": str(song_path), "lyrics_provenance": "auto_line"}
        mocks["k"].db.get_song_by_id.return_value = row
        mocks["k"].lyrics_service.dispatch_variant_fetch.return_value = True

        call_order: list = []
        mocks["k"].lyrics_service.dispatch_variant_fetch.side_effect = (
            lambda *_a, **_kw: call_order.append("dispatch") or True
        )
        mocks["k"].events.emit.side_effect = lambda *_a, **_kw: call_order.append("emit")

        r = _post_json(client, {"song_id": 1, "source": "AI"})

        assert r.status_code == 202
        assert call_order == [
            "dispatch",
            "emit",
        ], f"dispatch must precede lyrics_upgraded; got {call_order}"


class TestOff:
    def test_off_emits_subtitle_off_and_returns_ok(self, client, mocks, tmp_path):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        row = {"file_path": str(song_path), "lyrics_provenance": "auto_line"}
        mocks["k"].db.get_song_by_id.return_value = row

        r = _post_json(client, {"song_id": 1, "source": "off"})

        assert r.status_code == 200
        assert json.loads(r.data) == {"status": "ok"}
        mocks["k"].db.set_subtitle_source_override.assert_called_once_with(1, "off")
        # No fetch — off is a UI toggle.
        mocks["k"].lyrics_service.fetch_variant.assert_not_called()
        events = [c.args[0] for c in mocks["emit"].call_args_list]
        assert "subtitle_off" in events

    def test_off_to_source_emits_subtitle_on(self, client, mocks, tmp_path):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        variant = tmp_path / "Foo---abc.lrclib.ass"
        variant.write_text("[Script Info]\n")
        row = {"file_path": str(song_path), "lyrics_provenance": "auto_line"}
        mocks["k"].db.get_song_by_id.return_value = row
        mocks["k"].db.get_subtitle_source_override.return_value = "off"

        r = _post_json(client, {"song_id": 1, "source": "lrclib"})

        assert r.status_code == 200
        events = [c.args[0] for c in mocks["emit"].call_args_list]
        assert "subtitle_on" in events
        assert "subtitle_off" not in events


class TestUserSource:
    def test_user_blocked_when_no_user_authored_ass(self, client, mocks, tmp_path):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        # Provenance ≠ user → cannot pin user source.
        row = {"file_path": str(song_path), "lyrics_provenance": "auto_line"}
        mocks["k"].db.get_song_by_id.return_value = row

        r = _post_json(client, {"song_id": 1, "source": "user"})

        assert r.status_code == 400
        mocks["k"].db.set_subtitle_source_override.assert_not_called()

    def test_user_ok_when_user_authored(self, client, mocks, tmp_path):
        song_path = tmp_path / "Foo---abc.mp4"
        song_path.write_text("fake")
        row = {"file_path": str(song_path), "lyrics_provenance": "user"}
        mocks["k"].db.get_song_by_id.return_value = row

        r = _post_json(client, {"song_id": 1, "source": "user"})

        assert r.status_code == 200
        mocks["k"].db.set_subtitle_source_override.assert_called_once_with(1, "user")


class TestNotFound:
    def test_unknown_song_id_returns_404(self, client, mocks):
        mocks["k"].db.get_song_by_id.return_value = None
        r = _post_json(client, {"song_id": 99999, "source": "lrclib"})
        assert r.status_code == 404
