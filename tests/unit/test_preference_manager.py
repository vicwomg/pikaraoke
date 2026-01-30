"""Tests for the PreferenceManager."""

from __future__ import annotations

import os
import tempfile

import pytest

from pikaraoke.lib.preference_manager import PreferenceManager


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    fd, path = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


def test_preference_manager_get_nonexistent_preference(temp_config_file):
    """Test getting a preference that doesn't exist returns default value."""
    prefs = PreferenceManager(temp_config_file)
    result = prefs.get("nonexistent", "default")
    assert result == "default"


def test_preference_manager_get_nonexistent_preference_none(temp_config_file):
    """Test getting a preference that doesn't exist with no default returns None."""
    prefs = PreferenceManager(temp_config_file)
    result = prefs.get("nonexistent")
    assert result is None


def test_preference_manager_set_and_get(temp_config_file):
    """Test setting and getting a preference."""
    prefs = PreferenceManager(temp_config_file)

    success, message = prefs.set("test_pref", "test_value")
    assert success is True
    assert "successfully" in message.lower()

    result = prefs.get("test_pref")
    assert result == "test_value"


def test_preference_manager_type_conversion_bool_true(temp_config_file):
    """Test that boolean values are correctly converted (true variants)."""
    prefs = PreferenceManager(temp_config_file)

    for val in ["true", "True", "TRUE", "yes", "Yes", "on", "On"]:
        prefs.set("bool_pref", val)
        result = prefs.get("bool_pref")
        assert result is True, f"Failed for value: {val}"


def test_preference_manager_type_conversion_bool_false(temp_config_file):
    """Test that boolean values are correctly converted (false variants)."""
    prefs = PreferenceManager(temp_config_file)

    for val in ["false", "False", "FALSE", "no", "No", "off", "Off"]:
        prefs.set("bool_pref", val)
        result = prefs.get("bool_pref")
        assert result is False, f"Failed for value: {val}"


def test_preference_manager_type_conversion_int(temp_config_file):
    """Test that integer values are correctly converted."""
    prefs = PreferenceManager(temp_config_file)

    prefs.set("int_pref", "42")
    result = prefs.get("int_pref")
    assert result == 42
    assert isinstance(result, int)

    prefs.set("negative_int", "-10")
    result = prefs.get("negative_int")
    assert result == -10
    assert isinstance(result, int)


def test_preference_manager_type_conversion_float(temp_config_file):
    """Test that float values are correctly converted."""
    prefs = PreferenceManager(temp_config_file)

    prefs.set("float_pref", "3.14")
    result = prefs.get("float_pref")
    assert result == 3.14
    assert isinstance(result, float)

    prefs.set("negative_float", "-2.5")
    result = prefs.get("negative_float")
    assert result == -2.5
    assert isinstance(result, float)


def test_preference_manager_type_conversion_string(temp_config_file):
    """Test that string values remain as strings."""
    prefs = PreferenceManager(temp_config_file)

    prefs.set("string_pref", "hello world")
    result = prefs.get("string_pref")
    assert result == "hello world"
    assert isinstance(result, str)


def test_preference_manager_clear(temp_config_file):
    """Test clearing all preferences."""
    prefs = PreferenceManager(temp_config_file)

    # Set some preferences
    prefs.set("pref1", "value1")
    prefs.set("pref2", "value2")

    # Verify they exist
    assert prefs.get("pref1") == "value1"
    assert prefs.get("pref2") == "value2"

    # Clear preferences
    success, message = prefs.clear()
    assert success is True
    assert "successfully" in message.lower()

    # Verify config file is deleted
    assert not os.path.exists(temp_config_file)

    # Critical: verify cached values are cleared and defaults are returned
    assert prefs.get("pref1", "default1") == "default1"
    assert prefs.get("pref2", "default2") == "default2"
    assert prefs.get("volume", 1.0) == 1.0  # Should return fallback, not old value


def test_preference_manager_clear_nonexistent_file():
    """Test clearing preferences when config file doesn't exist.

    This is considered successful since the desired state (no config) is achieved.
    """
    prefs = PreferenceManager("/nonexistent/config.ini")

    success, message = prefs.clear()
    assert success is True
    assert "successfully" in message.lower()


def test_preference_manager_clear_resets_to_defaults(temp_config_file):
    """Test that clear() resets preferences to DEFAULTS, not just deletes file.

    This tests the bug where cached config object retained old values after file deletion.
    """
    prefs = PreferenceManager(temp_config_file)

    # Change some preferences from their defaults
    prefs.set("volume", "0.5")  # Default is 0.85
    prefs.set("browse_results_per_page", "100")  # Default is 500
    prefs.set("splash_delay", "10")  # Default is 2

    # Verify non-default values are stored
    assert prefs.get("volume") == 0.5
    assert prefs.get("browse_results_per_page") == 100
    assert prefs.get("splash_delay") == 10

    # Clear preferences
    success, message = prefs.clear()
    assert success is True

    # Verify preferences reset to defaults (using get_or_default)
    assert prefs.get_or_default("volume") == 0.85
    assert prefs.get_or_default("browse_results_per_page") == 500
    assert prefs.get_or_default("splash_delay") == 2


def test_preference_manager_multiple_preferences(temp_config_file):
    """Test managing multiple preferences."""
    prefs = PreferenceManager(temp_config_file)

    # Set multiple preferences of different types
    prefs.set("volume", "0.85")
    prefs.set("high_quality", "true")
    prefs.set("splash_delay", "5")
    prefs.set("logo_path", "/path/to/logo.png")

    # Verify all preferences are stored and converted correctly
    assert prefs.get("volume") == 0.85
    assert prefs.get("high_quality") is True
    assert prefs.get("splash_delay") == 5
    assert prefs.get("logo_path") == "/path/to/logo.png"


def test_preference_manager_overwrite_preference(temp_config_file):
    """Test overwriting an existing preference."""
    prefs = PreferenceManager(temp_config_file)

    prefs.set("test_pref", "original_value")
    assert prefs.get("test_pref") == "original_value"

    prefs.set("test_pref", "new_value")
    assert prefs.get("test_pref") == "new_value"


def test_preference_manager_persistence(temp_config_file):
    """Test that preferences persist across manager instances."""
    prefs1 = PreferenceManager(temp_config_file)
    prefs1.set("persistent_pref", "persistent_value")

    # Create a new manager instance with the same config file
    prefs2 = PreferenceManager(temp_config_file)
    result = prefs2.get("persistent_pref")

    assert result == "persistent_value"


def test_preference_manager_empty_string_value(temp_config_file):
    """Test that empty strings are handled correctly."""
    prefs = PreferenceManager(temp_config_file)

    prefs.set("empty_pref", "")
    result = prefs.get("empty_pref")
    assert result == ""
    assert isinstance(result, str)


def test_preference_manager_defaults_exist():
    """Test that DEFAULTS dictionary contains all expected preferences."""
    expected_keys = {
        "hide_url",
        "hide_notifications",
        "high_quality",
        "splash_delay",
        "volume",
        "normalize_audio",
        "complete_transcode_before_play",
        "buffer_size",
        "hide_overlay",
        "screensaver_timeout",
        "disable_bg_music",
        "bg_music_volume",
        "disable_bg_video",
        "disable_score",
        "limit_user_songs_by",
        "enable_fair_queue",
        "cdg_pixel_scaling",
        "avsync",
        "streaming_format",
        "browse_results_per_page",
        "low_score_phrases",
        "mid_score_phrases",
        "high_score_phrases",
    }

    assert set(PreferenceManager.DEFAULTS.keys()) == expected_keys


def test_preference_manager_defaults_types():
    """Test that DEFAULTS values have the correct types."""
    defaults = PreferenceManager.DEFAULTS

    # Boolean preferences
    assert isinstance(defaults["hide_url"], bool)
    assert isinstance(defaults["hide_notifications"], bool)
    assert isinstance(defaults["high_quality"], bool)
    assert isinstance(defaults["normalize_audio"], bool)
    assert isinstance(defaults["complete_transcode_before_play"], bool)
    assert isinstance(defaults["hide_overlay"], bool)
    assert isinstance(defaults["disable_bg_music"], bool)
    assert isinstance(defaults["disable_bg_video"], bool)
    assert isinstance(defaults["disable_score"], bool)
    assert isinstance(defaults["enable_fair_queue"], bool)
    assert isinstance(defaults["cdg_pixel_scaling"], bool)

    # Integer preferences
    assert isinstance(defaults["splash_delay"], int)
    assert isinstance(defaults["buffer_size"], int)
    assert isinstance(defaults["screensaver_timeout"], int)
    assert isinstance(defaults["limit_user_songs_by"], int)
    assert isinstance(defaults["avsync"], (int, float))

    # Float preferences
    assert isinstance(defaults["volume"], float)
    assert isinstance(defaults["bg_music_volume"], float)

    # String preferences
    assert isinstance(defaults["streaming_format"], str)
    assert isinstance(defaults["low_score_phrases"], str)
    assert isinstance(defaults["mid_score_phrases"], str)
    assert isinstance(defaults["high_score_phrases"], str)


def test_preference_manager_get_or_default_returns_default(temp_config_file):
    """Test get_or_default returns default when preference not in config."""
    prefs = PreferenceManager(temp_config_file)

    # Should return default value from DEFAULTS dictionary
    result = prefs.get_or_default("volume")
    assert result == 0.85

    result = prefs.get_or_default("high_quality")
    assert result is False

    result = prefs.get_or_default("splash_delay")
    assert result == 2


def test_preference_manager_get_or_default_returns_config_value(temp_config_file):
    """Test get_or_default returns config value when preference is set."""
    prefs = PreferenceManager(temp_config_file)

    # Set a preference
    prefs.set("volume", "0.5")
    prefs.set("high_quality", "true")
    prefs.set("splash_delay", "10")

    # Should return config value, not default
    assert prefs.get_or_default("volume") == 0.5
    assert prefs.get_or_default("high_quality") is True
    assert prefs.get_or_default("splash_delay") == 10


def test_preference_manager_get_or_default_unknown_key(temp_config_file):
    """Test get_or_default returns None for unknown preference keys."""
    prefs = PreferenceManager(temp_config_file)

    # Unknown key should return None (not in DEFAULTS)
    result = prefs.get_or_default("unknown_key")
    assert result is None


# --- Tests for Karaoke._load_preferences() integration ---


class MinimalKaraoke:
    """Minimal mock for testing _load_preferences() in isolation."""

    def __init__(self, config_file_path: str):
        self.preferences = PreferenceManager(config_file_path)

    # Import the actual _load_preferences method to test
    from pikaraoke.karaoke import Karaoke

    _load_preferences = Karaoke._load_preferences


def test_load_preferences_sets_all_instance_attributes(temp_config_file):
    """Test that _load_preferences sets all 23 preference attributes on the instance."""
    k = MinimalKaraoke(temp_config_file)

    # Call _load_preferences with no CLI overrides
    k._load_preferences()

    # Verify all DEFAULTS keys become instance attributes with default values
    for pref, default_value in PreferenceManager.DEFAULTS.items():
        assert hasattr(k, pref), f"Missing attribute: {pref}"
        assert getattr(k, pref) == default_value, f"Wrong value for {pref}"


def test_load_preferences_cli_overrides_config(temp_config_file):
    """Test that CLI args override config file values and persist to config."""
    k = MinimalKaraoke(temp_config_file)

    # Set a value in config file first
    k.preferences.set("volume", "0.5")
    k.preferences.set("splash_delay", "10")

    # Call _load_preferences with CLI overrides
    k._load_preferences(volume=0.9, splash_delay=5)

    # CLI values should override config values
    assert k.volume == 0.9
    assert k.splash_delay == 5

    # CLI values should be persisted to config
    assert k.preferences.get("volume") == 0.9
    assert k.preferences.get("splash_delay") == 5


def test_load_preferences_boolean_flag_handling(temp_config_file):
    """Test boolean CLI flag handling: True means flag was passed, False means not passed."""
    k = MinimalKaraoke(temp_config_file)

    # Set boolean preferences in config file
    k.preferences.set("normalize_audio", "true")
    k.preferences.set("high_quality", "true")

    # Call with normalize_audio=True (flag passed) and high_quality=False (flag not passed)
    # For booleans: True = flag was explicitly passed, False = flag was not passed
    k._load_preferences(normalize_audio=True, high_quality=False)

    # normalize_audio=True means flag was passed, so use CLI value (True)
    assert k.normalize_audio is True

    # high_quality=False means flag was NOT passed, so use config value (True)
    assert k.high_quality is True


def test_set_preserves_existing_preferences_across_instances(temp_config_file):
    """Regression test: set() should preserve existing preferences when called from new instance.

    Bug scenario (issue #XXX): Running `pikaraoke --hide-url` would wipe all other
    preferences from config.ini. This happened because hide_url is first in DEFAULTS,
    and set() didn't read the config file before writing.

    Steps to reproduce:
    1. Create config with multiple preferences
    2. Create NEW PreferenceManager instance (simulating app restart)
    3. Call set() on one preference
    4. Verify other preferences are preserved
    """
    # Setup: Create initial config with multiple preferences
    prefs1 = PreferenceManager(temp_config_file)
    prefs1.set("volume", "0.5")
    prefs1.set("splash_delay", "10")
    prefs1.set("high_quality", "true")

    # Verify initial state
    assert prefs1.get("volume") == 0.5
    assert prefs1.get("splash_delay") == 10
    assert prefs1.get("high_quality") is True

    # Simulate app restart: create new PreferenceManager instance
    prefs2 = PreferenceManager(temp_config_file)

    # Set a single preference (this would previously wipe others)
    prefs2.set("hide_url", "true")

    # Verify the new preference was set
    assert prefs2.get("hide_url") is True

    # CRITICAL: Verify other preferences are preserved (not wiped)
    assert prefs2.get("volume") == 0.5, "volume should be preserved"
    assert prefs2.get("splash_delay") == 10, "splash_delay should be preserved"
    assert prefs2.get("high_quality") is True, "high_quality should be preserved"

    # Verify persistence: create third instance and check again
    prefs3 = PreferenceManager(temp_config_file)
    assert prefs3.get("hide_url") is True
    assert prefs3.get("volume") == 0.5
    assert prefs3.get("splash_delay") == 10
    assert prefs3.get("high_quality") is True
