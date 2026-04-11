"""Minimal event system for decoupling components."""

from __future__ import annotations


class EventSystem:
    """Minimal event dispatcher to decouple components from socketio/flash.

    Handlers are called synchronously; exceptions bubble up normally.
    """

    def __init__(self):
        self._handlers: dict[str, list] = {}

    def on(self, event_name: str, handler) -> None:
        """Register a handler for an event."""
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler)

    def emit(self, event_name: str, *args, **kwargs) -> None:
        """Call all handlers registered for this event."""
        for handler in self._handlers.get(event_name, []):
            handler(*args, **kwargs)
