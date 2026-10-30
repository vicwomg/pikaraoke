"""Admin alerts: conditions that are currently true, not events that happened.

Keyed so raising one twice leaves one alert; the producer that raised it clears
it. See `admin-alert-system-2026-08-23.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

_SEVERITY_ORDER = {"danger": 0, "warning": 1}


@dataclass(frozen=True)
class Alert:
    """A condition that is currently true and worth an admin's attention."""

    key: str
    severity: str  # "warning" | "danger"
    message: str
    action_label: str | None = None
    # Root-relative path ("/queue"), absolute "http..." link, or None. With no
    # url the label is a command to copy.
    action_url: str | None = None


class AlertManager:
    """The active alerts, keyed for dedupe. No socket, no I/O: testable as is."""

    def __init__(self) -> None:
        self._alerts: dict[str, Alert] = {}

    def raise_alert(self, alert: Alert) -> bool:
        """Record an alert; return whether the active set changed."""
        if self._alerts.get(alert.key) == alert:
            return False
        self._alerts[alert.key] = alert
        return True

    def clear(self, key: str) -> bool:
        """Drop the alert with this key; return whether one was removed."""
        return self._alerts.pop(key, None) is not None

    def active(self) -> list[Alert]:
        """Active alerts, most severe first."""
        return sorted(self._alerts.values(), key=lambda a: _SEVERITY_ORDER.get(a.severity, 99))


def socket_payload(active: list[Alert]) -> dict:
    """The bell's live state: count and worst severity (None hides the bell)."""
    severity = None
    if any(a.severity == "danger" for a in active):
        severity = "danger"
    elif active:
        severity = "warning"
    return {"count": len(active), "severity": severity}
