"""Tests for preference routes — broadcast on change and reset."""

from unittest.mock import MagicMock, patch

import pytest
import werkzeug
from flask import Flask

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.preferences import preferences_bp

ROUTE_PREFIX = "pikaraoke.routes.preferences"


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.secret_key = "test"
    test_app.register_blueprint(preferences_bp)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def route_mocks():
    """Patch all external dependencies used by preference routes."""
    with (
        patch(f"{ROUTE_PREFIX}.get_karaoke_instance") as mock_get_instance,
        patch(f"{ROUTE_PREFIX}.is_admin", return_value=True),
        patch(f"{ROUTE_PREFIX}.broadcast_event") as mock_broadcast,
        patch(f"{ROUTE_PREFIX}._get_active_score_phrases") as mock_phrases,
    ):
        mock_k = MagicMock()
        mock_get_instance.return_value = mock_k
        yield {
            "karaoke": mock_k,
            "broadcast": mock_broadcast,
            "phrases": mock_phrases,
        }


class TestChangePreferencesBroadcast:
    """Tests that change_preferences broadcasts Socket.IO events."""

    def test_broadcasts_preferences_update_on_success(self, client, route_mocks):
        route_mocks["karaoke"].preferences.set.return_value = (True, "Success")

        client.get("/change_preferences?pref=disable_bg_video&val=True")

        route_mocks["broadcast"].assert_any_call(
            "preferences_update", {"key": "disable_bg_video", "value": "True"}
        )

    def test_does_not_broadcast_on_failure(self, client, route_mocks):
        route_mocks["karaoke"].preferences.set.return_value = (False, "Error")

        client.get("/change_preferences?pref=volume&val=0.5")

        route_mocks["broadcast"].assert_not_called()

    def test_score_phrase_change_broadcasts_score_phrases_update(self, client, route_mocks):
        route_mocks["karaoke"].preferences.set.return_value = (True, "Success")
        route_mocks["phrases"].return_value = {"low": ["Bad"], "mid": ["OK"], "high": ["Great"]}

        client.get("/change_preferences?pref=low_score_phrases&val=Bad")

        assert route_mocks["broadcast"].call_count == 2
        route_mocks["broadcast"].assert_any_call(
            "preferences_update", {"key": "low_score_phrases", "value": "Bad"}
        )
        route_mocks["broadcast"].assert_any_call(
            "score_phrases_update", {"low": ["Bad"], "mid": ["OK"], "high": ["Great"]}
        )

    def test_non_score_pref_does_not_broadcast_score_phrases(self, client, route_mocks):
        route_mocks["karaoke"].preferences.set.return_value = (True, "Success")

        client.get("/change_preferences?pref=hide_overlay&val=True")

        assert route_mocks["broadcast"].call_count == 1
        route_mocks["broadcast"].assert_called_once_with(
            "preferences_update", {"key": "hide_overlay", "value": "True"}
        )


class TestClearPreferencesBroadcast:
    """Tests that clear_preferences broadcasts Socket.IO events."""

    def test_broadcasts_reset_and_score_phrases_on_success(self, client, route_mocks):
        route_mocks["karaoke"].preferences.reset_all.return_value = (True, "Success")
        route_mocks["phrases"].return_value = {"low": ["L"], "mid": ["M"], "high": ["H"]}

        client.get("/clear_preferences", follow_redirects=False)

        route_mocks["broadcast"].assert_any_call("preferences_reset", PreferenceManager.DEFAULTS)
        route_mocks["broadcast"].assert_any_call(
            "score_phrases_update", {"low": ["L"], "mid": ["M"], "high": ["H"]}
        )

    def test_does_not_broadcast_on_reset_failure(self, client, route_mocks):
        route_mocks["karaoke"].preferences.reset_all.return_value = (False, "Error")

        client.get("/clear_preferences", follow_redirects=False)

        route_mocks["broadcast"].assert_not_called()
