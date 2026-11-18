"""Unit tests for the ytdl.broken alert reconciliation."""

from unittest.mock import patch

from pikaraoke.lib import ytdl_health
from pikaraoke.lib.events import EventSystem


def _capture(events):
    seen = []
    events.on("alert", lambda *a: seen.append(("alert", a)))
    events.on("alert_clear", lambda key: seen.append(("alert_clear", key)))
    return seen


class TestRaiseVariants:
    def test_broken_points_at_update_when_a_managed_binary_is_present(self, tmp_path):
        binary = tmp_path / "yt-dlp"
        binary.write_text("x")
        events = EventSystem()
        seen = _capture(events)
        with patch.object(ytdl_health, "binary_path", return_value=str(binary)):
            ytdl_health.raise_ytdl_broken(events)
        _, args = seen[0]
        assert args[0] == "ytdl.broken" and args[1] == "danger"
        assert args[4] == "#system"  # action_url jumps to the Update yt-dlp control

    def test_broken_points_at_pikaraoke_without_a_managed_binary(self, tmp_path):
        events = EventSystem()
        seen = _capture(events)
        with patch.object(ytdl_health, "binary_path", return_value=str(tmp_path / "absent")):
            ytdl_health.raise_ytdl_broken(events)
        _, args = seen[0]
        assert args[4].startswith("https://github.com/vicwomg/pikaraoke")

    def test_current_carries_no_action(self):
        events = EventSystem()
        seen = _capture(events)
        ytdl_health.raise_ytdl_current(events)
        _, args = seen[0]
        assert args[0] == "ytdl.broken"
        assert len(args) == 3  # key, severity, message only - no action


class TestReconcileAfterUpdate:
    def test_a_newer_version_clears(self):
        events = EventSystem()
        seen = _capture(events)
        ytdl_health.reconcile_after_update(events, "2024.01.01", "2024.10.07")
        assert seen == [("alert_clear", "ytdl.broken")]

    def test_an_unchanged_version_reshapes_to_already_current(self):
        events = EventSystem()
        seen = _capture(events)
        ytdl_health.reconcile_after_update(events, "2024.10.07", "2024.10.07")
        assert len(seen) == 1
        name, args = seen[0]
        assert name == "alert" and args[0] == "ytdl.broken" and len(args) == 3

    def test_a_failed_update_leaves_the_alert_untouched(self):
        events = EventSystem()
        seen = _capture(events)
        ytdl_health.reconcile_after_update(events, "2024.10.07", "Not found")
        assert seen == []
