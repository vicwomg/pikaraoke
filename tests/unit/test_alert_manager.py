"""Unit tests for the admin alert system: manager, broadcast and producers."""

from unittest.mock import MagicMock

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.alert_manager import Alert, AlertManager, socket_payload
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.library_scanner import ScanResult


class TestAlertManager:
    """The registry is a keyed dict, deduped and sorted danger-first."""

    def test_raise_twice_same_key_leaves_one_and_second_wins(self):
        manager = AlertManager()
        manager.raise_alert(Alert("ytdl.broken", "danger", "first"))
        manager.raise_alert(Alert("ytdl.broken", "danger", "second"))

        active = manager.active()
        assert len(active) == 1
        assert active[0].message == "second"

    def test_clear_absent_key_is_a_noop(self):
        manager = AlertManager()
        manager.clear("never.raised")  # must not raise
        assert manager.active() == []

    def test_clear_removes_the_alert(self):
        manager = AlertManager()
        manager.raise_alert(Alert("downloads.failed", "warning", "one"))
        manager.clear("downloads.failed")
        assert manager.active() == []

    def test_active_orders_danger_before_warning(self):
        manager = AlertManager()
        manager.raise_alert(Alert("version.available", "warning", "w"))
        manager.raise_alert(Alert("ytdl.broken", "danger", "d"))

        keys = [a.key for a in manager.active()]
        assert keys == ["ytdl.broken", "version.available"]

    def test_raise_returns_whether_the_active_set_changed(self):
        manager = AlertManager()
        alert = Alert("downloads.failed", "warning", "1 download failed")
        assert manager.raise_alert(alert) is True
        assert manager.raise_alert(alert) is False  # identical, no change
        assert manager.raise_alert(Alert("downloads.failed", "warning", "2")) is True

    def test_clear_returns_whether_one_was_removed(self):
        manager = AlertManager()
        assert manager.clear("absent") is False
        manager.raise_alert(Alert("downloads.failed", "warning", "1"))
        assert manager.clear("downloads.failed") is True


class TestSocketPayload:
    """The bell's live state: count and the most severe colour."""

    def test_empty_is_all_clear(self):
        assert socket_payload([]) == {"count": 0, "severity": None}

    def test_count_and_warning_when_no_danger(self):
        active = [Alert("a", "warning", "a"), Alert("b", "warning", "b")]
        assert socket_payload(active) == {"count": 2, "severity": "warning"}

    def test_any_danger_colours_the_whole_badge(self):
        active = [Alert("a", "warning", "a"), Alert("b", "danger", "b")]
        assert socket_payload(active) == {"count": 2, "severity": "danger"}


def _bare_karaoke(socketio=None):
    """A Karaoke with only the alert wiring, bypassing the heavy __init__."""
    k = Karaoke.__new__(Karaoke)
    k.events = EventSystem()
    k.alerts = AlertManager()
    k.socketio = socketio
    k._wire_alerts()
    return k


class TestKaraokeAlertBroadcast:
    """Emitting alert/alert_clear drives the manager and the socket broadcast."""

    def test_raise_updates_manager_and_broadcasts(self):
        socketio = MagicMock()
        k = _bare_karaoke(socketio)

        k.events.emit("alert", "downloads.failed", "warning", "1 download failed", "View", "/queue")

        assert [a.key for a in k.alerts.active()] == ["downloads.failed"]
        socketio.emit.assert_called_once_with(
            "alerts", {"count": 1, "severity": "warning"}, namespace="/"
        )

    def test_clear_broadcasts_all_clear(self):
        socketio = MagicMock()
        k = _bare_karaoke(socketio)
        k.events.emit("alert", "ytdl.broken", "danger", "broken")
        socketio.emit.reset_mock()

        k.events.emit("alert_clear", "ytdl.broken")

        assert k.alerts.active() == []
        socketio.emit.assert_called_once_with(
            "alerts", {"count": 0, "severity": None}, namespace="/"
        )

    def test_top_severity_follows_the_worst_active(self):
        socketio = MagicMock()
        k = _bare_karaoke(socketio)

        k.events.emit("alert", "downloads.failed", "warning", "w")
        k.events.emit("alert", "ytdl.broken", "danger", "d")

        assert socketio.emit.call_args.args[1] == {"count": 2, "severity": "danger"}

    def test_no_socketio_is_tolerated(self):
        k = _bare_karaoke(socketio=None)
        k.events.emit("alert", "downloads.failed", "warning", "w")  # must not raise
        assert len(k.alerts.active()) == 1

    def test_clearing_an_absent_alert_does_not_broadcast(self):
        socketio = MagicMock()
        k = _bare_karaoke(socketio)
        k.events.emit("alert_clear", "never.raised")
        socketio.emit.assert_not_called()

    def test_reraising_an_identical_alert_does_not_broadcast(self):
        socketio = MagicMock()
        k = _bare_karaoke(socketio)
        k.events.emit("alert", "ytdl.broken", "danger", "broken", None, "#system")
        socketio.emit.reset_mock()
        k.events.emit("alert", "ytdl.broken", "danger", "broken", None, "#system")
        socketio.emit.assert_not_called()


class TestApplyScanResultAlert:
    """library.unmounted raises on a tripped breaker, clears on a clean scan."""

    def _karaoke_with_captured_alerts(self):
        k = Karaoke.__new__(Karaoke)
        k.events = EventSystem()
        k.alerts = AlertManager()
        k.socketio = None
        k.download_path = "/songs"
        k.song_manager = MagicMock()
        k.db = MagicMock()
        k._wire_alerts()
        return k

    def test_tripped_breaker_raises_danger(self):
        k = self._karaoke_with_captured_alerts()

        k._apply_scan_result(ScanResult(added=0, moved=0, deleted=0, circuit_tripped=True))

        active = k.alerts.active()
        assert [a.key for a in active] == ["library.unmounted"]
        assert active[0].severity == "danger"

    def test_clean_scan_clears(self):
        k = self._karaoke_with_captured_alerts()
        k._apply_scan_result(ScanResult(added=0, moved=0, deleted=0, circuit_tripped=True))

        k._apply_scan_result(ScanResult(added=3, moved=0, deleted=0, circuit_tripped=False))

        assert k.alerts.active() == []
