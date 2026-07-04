"""Prevent the host machine from sleeping while PiKaraoke is running.

In headless mode there is no local player window holding an OS wake lock, so the
host can idle-sleep and interrupt streaming to connected clients. This keeps the
system awake using each platform's native mechanism.
"""

import ctypes
import logging
import shutil
import subprocess

from pikaraoke.lib.get_platform import is_linux, is_macos, is_windows

# Windows SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """Keeps the host awake for the app's lifetime via a native OS mechanism.

    Windows uses SetThreadExecutionState; macOS uses ``caffeinate``; Linux uses
    ``systemd-inhibit``. ``start()``/``stop()`` are safe to call on any platform
    and no-op cleanly when the mechanism is unavailable.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        """Begin preventing system (and display) sleep."""
        if is_windows():
            self._start_windows()
        elif is_macos():
            # -d prevents display sleep, -i idle sleep, -s system sleep.
            self._start_subprocess(["caffeinate", "-dis"], "caffeinate")
        elif is_linux():
            self._start_linux()
        else:
            logging.warning("Keep-awake is not supported on this platform; ignoring.")

    def stop(self) -> None:
        """Release the wake lock, allowing normal power management to resume."""
        if is_windows():
            self._stop_windows()
        elif self._process is not None:
            logging.info("Stopping keep-awake process")
            self._process.terminate()
            self._process.wait()
            self._process = None

    # --- Windows ---

    def _start_windows(self) -> None:
        # ES_CONTINUOUS keeps the requirement in effect for the life of this
        # (main) thread, so a single call holds until stop() or process exit.
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
        )
        if result == 0:
            logging.error("Failed to set Windows execution state for keep-awake")
        else:
            logging.info("Keep-awake enabled (system will not sleep)")

    def _stop_windows(self) -> None:
        # ES_CONTINUOUS alone clears the previous system/display requirements.
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)

    # --- Linux ---

    def _start_linux(self) -> None:
        if shutil.which("systemd-inhibit") is None:
            logging.warning(
                "Keep-awake requested but 'systemd-inhibit' was not found. "
                "Disable sleep manually in your OS power settings."
            )
            return
        self._start_subprocess(
            [
                "systemd-inhibit",
                # Inhibit only idle (not sleep): blocking idle prevents the
                # automatic idle->suspend that interrupts streaming, and unlike
                # --what=sleep it needs no privileged polkit auth (which would
                # prompt for a password on headless/SSH sessions).
                "--what=idle",
                "--who=PiKaraoke",
                "--why=Karaoke session active",
                "--mode=block",
                "sleep",
                "infinity",
            ],
            "systemd-inhibit",
        )

    # --- macOS / Linux shared subprocess helper ---

    def _start_subprocess(self, cmd: list[str], name: str) -> None:
        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            logging.info(f"Keep-awake enabled via {name} (system will not sleep)")
        except OSError as e:
            logging.error(f"Failed to start keep-awake process '{name}': {e}")
