"""Unit tests for the periodic PiKaraoke version check (version.available)."""

from unittest.mock import patch

import pytest

from pikaraoke.lib import version_check
from pikaraoke.lib.events import EventSystem


def _capture(events):
    """Record alert / alert_clear emissions as (name, args) tuples."""
    seen = []
    events.on("alert", lambda *a: seen.append(("alert", a)))
    events.on("alert_clear", lambda *a: seen.append(("alert_clear", a)))
    return seen


class TestVersionParsing:
    def test_strips_leading_v_and_compares_numerically(self):
        assert version_check._is_newer("v1.24.0", "1.23.0")
        assert version_check._is_newer("1.23.1", "1.23.0")

    def test_equal_or_older_is_not_newer(self):
        assert not version_check._is_newer("1.23.0", "1.23.0")
        assert not version_check._is_newer("1.22.9", "1.23.0")

    def test_unparseable_tag_is_never_newer(self):
        assert not version_check._is_newer("nightly", "1.23.0")


class TestCheckForUpdate:
    def test_unreachable_api_raises_nothing(self):
        events = EventSystem()
        seen = _capture(events)
        with patch.object(version_check, "_fetch_latest_version", return_value=None):
            version_check.check_for_update(events)
        assert seen == []

    def test_equal_version_raises_nothing(self):
        events = EventSystem()
        seen = _capture(events)
        with patch.object(version_check, "_fetch_latest_version", return_value="1.23.0"):
            with patch.object(version_check, "VERSION", "1.23.0"):
                version_check.check_for_update(events)
        assert seen == []

    def test_newer_version_raises_version_available(self):
        events = EventSystem()
        seen = _capture(events)
        with patch.object(version_check, "_fetch_latest_version", return_value="9.9.9"):
            with patch.object(version_check, "VERSION", "1.23.0"):
                with patch.object(version_check, "is_windows", return_value=False):
                    with patch.object(version_check, "is_running_in_docker", return_value=False):
                        version_check.check_for_update(events)
        assert len(seen) == 1
        name, args = seen[0]
        assert name == "alert"
        assert args[0] == "version.available"
        assert args[1] == "warning"


class TestUpgradeAction:
    """One condition, three messages: a link on Windows, a command elsewhere."""

    def test_windows_links_to_the_release_page(self):
        with patch.object(version_check, "is_windows", return_value=True):
            label, url = version_check._upgrade_action("9.9.9")
        assert url == version_check._RELEASES_PAGE

    def test_container_offers_a_compose_command_not_a_link(self):
        with patch.object(version_check, "is_windows", return_value=False):
            with patch.object(version_check, "is_running_in_docker", return_value=True):
                label, url = version_check._upgrade_action("9.9.9")
        assert url is None
        assert "docker compose" in label

    def test_everything_else_offers_uv_tool_upgrade(self):
        with patch.object(version_check, "is_windows", return_value=False):
            with patch.object(version_check, "is_running_in_docker", return_value=False):
                label, url = version_check._upgrade_action("9.9.9")
        assert url is None
        assert label == "uv tool upgrade pikaraoke"


class TestTestSeam:
    def test_env_var_substitutes_version_without_network(self, monkeypatch):
        events = EventSystem()
        seen = _capture(events)
        monkeypatch.setenv(version_check._FAKE_LATEST_ENV, "99.0.0")
        with patch.object(version_check, "_fetch_latest_version") as fetch:
            with patch.object(version_check, "is_windows", return_value=False):
                with patch.object(version_check, "is_running_in_docker", return_value=False):
                    version_check.check_for_update(events)
        fetch.assert_not_called()
        assert seen and seen[0][1][0] == "version.available"

    def test_unset_env_var_leaves_the_check_on_the_network(self, monkeypatch):
        events = EventSystem()
        _capture(events)
        monkeypatch.delenv(version_check._FAKE_LATEST_ENV, raising=False)
        with patch.object(version_check, "_fetch_latest_version", return_value=None) as fetch:
            version_check.check_for_update(events)
        fetch.assert_called_once()
