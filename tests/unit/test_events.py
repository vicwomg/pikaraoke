"""Tests for the EventSystem."""

from __future__ import annotations

from pikaraoke.lib.events import EventSystem


def test_event_system_emit_calls_handler():
    """Test that emitting an event calls the registered handler."""
    events = EventSystem()
    captured_events = []

    events.on("test_event", lambda msg: captured_events.append(msg))
    events.emit("test_event", "test message")

    assert len(captured_events) == 1
    assert captured_events[0] == "test message"


def test_event_system_multiple_handlers():
    """Test that multiple handlers can be registered for the same event."""
    events = EventSystem()
    captured_events_1 = []
    captured_events_2 = []

    events.on("test_event", lambda msg: captured_events_1.append(msg))
    events.on("test_event", lambda msg: captured_events_2.append(msg))
    events.emit("test_event", "test message")

    assert len(captured_events_1) == 1
    assert len(captured_events_2) == 1
    assert captured_events_1[0] == "test message"
    assert captured_events_2[0] == "test message"


def test_event_system_multiple_args():
    """Test that events can be emitted with multiple arguments."""
    events = EventSystem()
    captured_args = []

    events.on("test_event", lambda *args: captured_args.extend(args))
    events.emit("test_event", "arg1", "arg2", "arg3")

    assert len(captured_args) == 3
    assert captured_args == ["arg1", "arg2", "arg3"]


def test_event_system_kwargs():
    """Test that events can be emitted with keyword arguments."""
    events = EventSystem()
    captured_kwargs = {}

    events.on("test_event", lambda **kwargs: captured_kwargs.update(kwargs))
    events.emit("test_event", key1="value1", key2="value2")

    assert len(captured_kwargs) == 2
    assert captured_kwargs["key1"] == "value1"
    assert captured_kwargs["key2"] == "value2"


def test_event_system_no_handlers():
    """Test that emitting an event with no handlers doesn't raise an error."""
    events = EventSystem()
    # Should not raise any exception
    events.emit("nonexistent_event", "test message")


def test_event_system_handler_exceptions_bubble_up():
    """Test that exceptions in handlers are not caught."""
    import pytest

    events = EventSystem()

    def failing_handler(msg):
        raise ValueError("Handler failed")

    events.on("test_event", failing_handler)

    with pytest.raises(ValueError, match="Handler failed"):
        events.emit("test_event", "test message")
