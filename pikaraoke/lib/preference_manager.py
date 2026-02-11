"""User preferences management with config file persistence."""

from __future__ import annotations

import configparser
import logging
import os
import shutil
from typing import Any

from flask_babel import _

from pikaraoke.lib.get_platform import get_data_directory


class PreferenceManager:
    """Single source of truth for user-configurable settings.

    Stores settings in config.ini under [USERPREFERENCES] section.
    Handles persistence, type conversion, and legacy config migration.
    """

    # Default values for all user preferences (single source of truth)
    DEFAULTS = {
        "hide_url": False,
        "hide_notifications": False,
        "high_quality": False,
        "splash_delay": 2,
        "volume": 0.85,
        "normalize_audio": False,
        "complete_transcode_before_play": False,
        "buffer_size": 150,
        "hide_overlay": False,
        "screensaver_timeout": 300,
        "disable_bg_music": False,
        "bg_music_volume": 0.3,
        "disable_bg_video": False,
        "disable_score": False,
        "limit_user_songs_by": 0,
        "enable_fair_queue": False,
        "cdg_pixel_scaling": False,
        "avsync": 0,
        "browse_results_per_page": 500,
        "low_score_phrases": "",
        "mid_score_phrases": "",
        "high_score_phrases": "",
        "show_splash_clock": False,
    }

    def __init__(self, config_file_path: str = "config.ini", target: object | None = None) -> None:
        """Initialize with config path and optional target object to sync.

        Args:
            config_file_path: Path to config.ini (relative paths go in data directory)
            target: Optional object to sync attributes on when preferences change
        """
        self._config_obj = configparser.ConfigParser()
        self._target = target  # Object to sync attributes on

        # Migrate config.ini from old default to new data directory
        if config_file_path == "config.ini":
            self._migrate_legacy_config()

        # Set the config file path
        if not os.path.isabs(config_file_path):
            self.config_file_path = os.path.join(get_data_directory(), config_file_path)
        else:
            self.config_file_path = config_file_path

        logging.debug(f"Using config file: {self.config_file_path}")

    def _migrate_legacy_config(self) -> None:
        """Migrate config.ini from cwd to data directory for backward compatibility."""
        legacy_config = "config.ini"  # Represents ./config.ini
        new_config_dir = get_data_directory()
        new_config_path = os.path.join(new_config_dir, "config.ini")

        # Move only if legacy exists and new one does not (don't overwrite)
        if os.path.exists(legacy_config) and not os.path.exists(new_config_path):
            logging.info(f"Migrating legacy config.ini from {os.getcwd()} to {new_config_dir}")
            try:
                shutil.move(legacy_config, new_config_path)
            except OSError as e:
                logging.error(f"Failed to migrate config file: {e}")

    def get(self, preference: str, default_value: Any = None) -> Any:
        """Get a preference value, auto-converting to bool/int/float."""
        # Silently ignores missing files
        self._config_obj.read(self.config_file_path, encoding="utf-8")

        if not self._config_obj.has_section("USERPREFERENCES"):
            return default_value

        try:
            pref = self._config_obj.get("USERPREFERENCES", preference)
            return self._convert_value(pref)
        except (configparser.NoOptionError, ValueError):
            return default_value

    def get_or_default(self, preference: str) -> Any:
        """Get a preference value, falling back to DEFAULTS if not set."""
        return self.get(preference, self.DEFAULTS.get(preference))

    def set(self, preference: str, val: Any) -> tuple[bool, str]:
        """Update a preference, persist to config, and sync target object.

        Returns (success, message) tuple.
        """
        logging.debug(f"Changing user preference << {preference} >> to {val}")
        try:
            # Read existing config to preserve other preferences
            self._config_obj.read(self.config_file_path, encoding="utf-8")

            if "USERPREFERENCES" not in self._config_obj:
                self._config_obj.add_section("USERPREFERENCES")

            userprefs = self._config_obj["USERPREFERENCES"]
            userprefs[preference] = str(val)

            with open(self.config_file_path, "w", encoding="utf-8") as conf:
                self._config_obj.write(conf)

            # Auto-sync target object if registered
            if self._target is not None:
                typed_val = self._convert_value(val)
                setattr(self._target, preference, typed_val)

            return (True, _("Your preferences were changed successfully"))
        except Exception as e:
            logging.debug(f"Failed to change user preference << {preference} >>: {e}")
            return (False, _("Something went wrong! Your preferences were not changed"))

    def clear(self) -> tuple[bool, str]:
        """Remove all user preferences by deleting the config file. Returns (success, message)."""
        try:
            if os.path.exists(self.config_file_path):
                os.remove(self.config_file_path)
                logging.info(f"Cleared preferences: deleted {self.config_file_path}")
            # Clear cached config object to prevent stale values from persisting
            self._config_obj.clear()
            return (True, _("Your preferences were cleared successfully"))
        except OSError as e:
            logging.error(f"Failed to clear preferences: {e}")
            return (False, _("Something went wrong! Your preferences were not cleared"))

    def _convert_value(self, val: Any) -> Any:
        """Convert a string to bool/int/float if applicable, otherwise return as-is."""
        if not isinstance(val, str):
            return val

        val_lower = val.lower()
        if val_lower in ("true", "yes", "on"):
            return True
        if val_lower in ("false", "no", "off"):
            return False

        # Try numeric conversion: integer first, then float
        stripped = val.lstrip("-")
        if stripped.isdigit():
            return int(val)
        if stripped.replace(".", "", 1).isdigit():
            return float(val)

        return val

    def apply_all(self, **cli_overrides: Any) -> None:
        """Hydrate target object with all preferences from config/defaults.

        Priority: CLI argument (if provided) > config file > DEFAULTS

        CLI arguments that are explicitly provided are persisted to config
        so the Web UI reflects the startup flags.

        Args:
            **cli_overrides: CLI arguments that should override config values
        """
        if self._target is None:
            return

        for pref, default in self.DEFAULTS.items():
            cli_value = cli_overrides.get(pref)

            # CLI is "provided" if: boolean flag is True, or non-boolean has a value
            # (Boolean flags use store_true, so False means "not passed")
            cli_provided = cli_value is True if isinstance(default, bool) else cli_value is not None

            if cli_provided:
                setattr(self._target, pref, cli_value)
                self.set(pref, cli_value)  # Persist CLI arg to config
            else:
                setattr(self._target, pref, self.get(pref, default))

    def reset_all(self) -> tuple[bool, str]:
        """Clear config file and reset target to defaults.

        Returns (success, message) tuple.
        """
        success, message = self.clear()
        if success and self._target is not None:
            for pref, default in self.DEFAULTS.items():
                setattr(self._target, pref, default)
        return success, message
