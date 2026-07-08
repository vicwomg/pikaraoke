"""Tests for SoundManager feature-flag gating."""

from pikaraoke.lib import sound_manager as sm
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager


def _make_manager(tmp_path, enabled):
    preferences = PreferenceManager(config_file_path=str(tmp_path / "config.ini"))
    return sm.SoundManager(preferences=preferences, events=EventSystem(), enabled=enabled)


def test_unavailable_when_disabled_even_with_backend(tmp_path, monkeypatch):
    """available is False when the feature flag is off, regardless of backend."""
    monkeypatch.setattr(sm, "_HAS_PACTL", True)
    monkeypatch.setattr(sm, "_SOUNDDEVICE_AVAILABLE", True)

    manager = _make_manager(tmp_path, enabled=False)

    assert manager.available is False


def test_available_when_enabled_with_backend(tmp_path, monkeypatch):
    """available is True when enabled and a backend is present."""
    monkeypatch.setattr(sm, "_HAS_PACTL", True)
    monkeypatch.setattr(sm, "_SOUNDDEVICE_AVAILABLE", False)

    manager = _make_manager(tmp_path, enabled=True)

    assert manager.available is True


def test_unavailable_when_enabled_without_backend(tmp_path, monkeypatch):
    """available is False when enabled but no backend is present."""
    monkeypatch.setattr(sm, "_HAS_PACTL", False)
    monkeypatch.setattr(sm, "_SOUNDDEVICE_AVAILABLE", False)

    manager = _make_manager(tmp_path, enabled=True)

    assert manager.available is False


def test_activate_refused_when_disabled(tmp_path, monkeypatch):
    """activate() refuses to start a stream when the feature is disabled."""
    monkeypatch.setattr(sm, "_HAS_PACTL", False)
    monkeypatch.setattr(sm, "_SOUNDDEVICE_AVAILABLE", True)

    manager = _make_manager(tmp_path, enabled=False)

    assert manager.activate("0", 1.0) is False


def test_enumerate_empty_when_disabled(tmp_path, monkeypatch):
    """enumerate_devices() returns nothing when the feature is disabled."""
    monkeypatch.setattr(sm, "_HAS_PACTL", True)
    monkeypatch.setattr(sm, "_SOUNDDEVICE_AVAILABLE", True)

    manager = _make_manager(tmp_path, enabled=False)

    assert manager.enumerate_devices() == []


def test_start_noop_when_disabled(tmp_path, monkeypatch):
    """start() does not enumerate devices when the feature is disabled."""
    monkeypatch.setattr(sm, "_HAS_PACTL", True)
    monkeypatch.setattr(sm, "_SOUNDDEVICE_AVAILABLE", True)

    manager = _make_manager(tmp_path, enabled=False)
    called = False

    def fail():
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(manager, "enumerate_devices", fail)
    manager.start()

    assert called is False
