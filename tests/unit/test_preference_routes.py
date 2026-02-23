"""Tests for preference routes — broadcast on change and reset."""

from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.preferences import preferences_bp


@pytest.fixture
def app():
    """Create a Flask app for testing."""
    test_app = Flask(__name__)
    test_app.secret_key = "test"
    test_app.register_blueprint(preferences_bp)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestChangePreferencesBroadcast:
    """Tests that change_preferences broadcasts Socket.IO events."""

    @patch("pikaraoke.routes.preferences._get_active_score_phrases")
    @patch("pikaraoke.routes.preferences.broadcast_event")
    @patch("pikaraoke.routes.preferences.is_admin", return_value=True)
    @patch("pikaraoke.routes.preferences.get_karaoke_instance")
    def test_broadcasts_preferences_update_on_success(
        self, mock_get_instance, _mock_admin, mock_broadcast, _mock_phrases, client
    ):
        mock_k = MagicMock()
        mock_k.preferences.set.return_value = (True, "Success")
        mock_get_instance.return_value = mock_k

        client.get("/change_preferences?pref=disable_bg_video&val=True")

        mock_broadcast.assert_any_call(
            "preferences_update", {"key": "disable_bg_video", "value": "True"}
        )

    @patch("pikaraoke.routes.preferences._get_active_score_phrases")
    @patch("pikaraoke.routes.preferences.broadcast_event")
    @patch("pikaraoke.routes.preferences.is_admin", return_value=True)
    @patch("pikaraoke.routes.preferences.get_karaoke_instance")
    def test_does_not_broadcast_on_failure(
        self, mock_get_instance, _mock_admin, mock_broadcast, _mock_phrases, client
    ):
        mock_k = MagicMock()
        mock_k.preferences.set.return_value = (False, "Error")
        mock_get_instance.return_value = mock_k

        client.get("/change_preferences?pref=volume&val=0.5")

        mock_broadcast.assert_not_called()

    @patch("pikaraoke.routes.preferences._get_active_score_phrases")
    @patch("pikaraoke.routes.preferences.broadcast_event")
    @patch("pikaraoke.routes.preferences.is_admin", return_value=True)
    @patch("pikaraoke.routes.preferences.get_karaoke_instance")
    def test_score_phrase_change_broadcasts_score_phrases_update(
        self, mock_get_instance, _mock_admin, mock_broadcast, mock_phrases, client
    ):
        mock_k = MagicMock()
        mock_k.preferences.set.return_value = (True, "Success")
        mock_get_instance.return_value = mock_k
        mock_phrases.return_value = {"low": ["Bad"], "mid": ["OK"], "high": ["Great"]}

        client.get("/change_preferences?pref=low_score_phrases&val=Bad")

        assert mock_broadcast.call_count == 2
        mock_broadcast.assert_any_call(
            "preferences_update", {"key": "low_score_phrases", "value": "Bad"}
        )
        mock_broadcast.assert_any_call(
            "score_phrases_update", {"low": ["Bad"], "mid": ["OK"], "high": ["Great"]}
        )

    @patch("pikaraoke.routes.preferences._get_active_score_phrases")
    @patch("pikaraoke.routes.preferences.broadcast_event")
    @patch("pikaraoke.routes.preferences.is_admin", return_value=True)
    @patch("pikaraoke.routes.preferences.get_karaoke_instance")
    def test_non_score_pref_does_not_broadcast_score_phrases(
        self, mock_get_instance, _mock_admin, mock_broadcast, _mock_phrases, client
    ):
        mock_k = MagicMock()
        mock_k.preferences.set.return_value = (True, "Success")
        mock_get_instance.return_value = mock_k

        client.get("/change_preferences?pref=hide_overlay&val=True")

        assert mock_broadcast.call_count == 1
        mock_broadcast.assert_called_once_with(
            "preferences_update", {"key": "hide_overlay", "value": "True"}
        )


class TestClearPreferencesBroadcast:
    """Tests that clear_preferences broadcasts Socket.IO events."""

    @patch("pikaraoke.routes.preferences._get_active_score_phrases")
    @patch("pikaraoke.routes.preferences.broadcast_event")
    @patch("pikaraoke.routes.preferences.is_admin", return_value=True)
    @patch("pikaraoke.routes.preferences.get_karaoke_instance")
    def test_broadcasts_reset_and_score_phrases_on_success(
        self, mock_get_instance, _mock_admin, mock_broadcast, mock_phrases, client
    ):
        mock_k = MagicMock()
        mock_k.preferences.reset_all.return_value = (True, "Success")
        mock_get_instance.return_value = mock_k
        mock_phrases.return_value = {"low": ["L"], "mid": ["M"], "high": ["H"]}

        client.get("/clear_preferences", follow_redirects=False)

        mock_broadcast.assert_any_call("preferences_reset", PreferenceManager.DEFAULTS)
        mock_broadcast.assert_any_call(
            "score_phrases_update", {"low": ["L"], "mid": ["M"], "high": ["H"]}
        )

    @patch("pikaraoke.routes.preferences._get_active_score_phrases")
    @patch("pikaraoke.routes.preferences.broadcast_event")
    @patch("pikaraoke.routes.preferences.is_admin", return_value=True)
    @patch("pikaraoke.routes.preferences.get_karaoke_instance")
    def test_does_not_broadcast_on_reset_failure(
        self, mock_get_instance, _mock_admin, mock_broadcast, _mock_phrases, client
    ):
        mock_k = MagicMock()
        mock_k.preferences.reset_all.return_value = (False, "Error")
        mock_get_instance.return_value = mock_k

        client.get("/clear_preferences", follow_redirects=False)

        mock_broadcast.assert_not_called()
