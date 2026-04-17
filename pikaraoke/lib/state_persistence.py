"""Runtime state persistence across restarts.

Captures queue, now-playing, and master volume to a JSON file so the app
can resume after a restart (watchfiles dev reload, crash, or manual stop).
Preferences already persist via PreferenceManager/config.ini; this file is
only for state that does not belong in user preferences.
"""

import json
import logging
import os
import tempfile
from typing import Any

from pikaraoke.lib.get_platform import get_data_directory

SCHEMA_VERSION = 1
_FILENAME = "pikaraoke_state.json"


class StatePersistence:
    """Atomic JSON snapshot of runtime state, saved next to config.ini."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(get_data_directory(), _FILENAME)

    def save(self, state: dict[str, Any]) -> None:
        """Atomically write state to disk. Errors are logged, not raised."""
        payload = {"version": SCHEMA_VERSION, **state}
        directory = os.path.dirname(self.path) or "."
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".pikaraoke_state.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, self.path)
            tmp_path = None
        except OSError as e:
            logging.error(f"Failed to persist state to {self.path}: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def load(self) -> dict[str, Any] | None:
        """Read state. Returns None when missing, unreadable, or unknown version."""
        if not os.path.isfile(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Ignoring corrupt state file {self.path}: {e}")
            return None

        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
            logging.warning(
                f"Ignoring state file {self.path}: unsupported version {data.get('version') if isinstance(data, dict) else '?'}"
            )
            return None
        return data
