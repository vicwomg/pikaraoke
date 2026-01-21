"""Browser utilities for launching the splash screen."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import webbrowser
from typing import TYPE_CHECKING

from pikaraoke.lib.get_platform import (
    get_data_directory,
    is_linux,
    is_macos,
    is_windows,
)

if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


class Browser:
    def __init__(
        self, karaoke: Karaoke, window_size: str | None = None, external_monitor: bool = False
    ):
        """Initialize the browser with the splash screen in kiosk mode.

        Args:
            karaoke: Karaoke instance with URL and platform configuration.
            window_size: Optional window geometry as "width,height" string.
            external_monitor: If True, position window on external monitor.
        """
        self.karaoke = karaoke
        self.window_size = window_size
        self.external_monitor = external_monitor
        self.browser_process: subprocess.Popen | None = None
        self.browser_profile_dir = os.path.join(get_data_directory(), "browser_profile")
        self.splash_url = f"{self.karaoke.url}/splash"

    def launch_splash_screen(self) -> subprocess.Popen | None:
        """Launch the browser with the splash screen in kiosk mode.

        Uses a persistent user data directory to ensure kiosk flags are respected
        even if another browser instance is running, and to preserve cookies
        (such as user name) across restarts. Supports Chrome, Chromium, and Edge
        on Windows, Linux, and macOS.
        """
        logging.debug(f"Launching splash screen: {self.splash_url}")

        suppress_logs = int(self.karaoke.log_level) > logging.DEBUG
        stdout_dest = subprocess.DEVNULL if suppress_logs else None
        stderr_dest = subprocess.DEVNULL if suppress_logs else None

        # Browser candidates ordered by preference (Chrome/Chromium/Edge only)
        candidates = []

        if is_windows():
            prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            local_app_data = os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")

            candidates.extend(
                [
                    os.path.join(prog_files, r"Google\Chrome\Application\chrome.exe"),
                    os.path.join(prog_files_x86, r"Google\Chrome\Application\chrome.exe"),
                    os.path.join(local_app_data, r"Google\Chrome\Application\chrome.exe"),
                    os.path.join(prog_files, r"Microsoft\Edge\Application\msedge.exe"),
                    os.path.join(prog_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
                ]
            )
        elif is_linux():
            candidates.extend(
                [
                    "chromium-browser",
                    "chromium",
                    "google-chrome",
                    "chrome",
                ]
            )
        elif is_macos():
            candidates.extend(
                [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]
            )

        # Find first available browser binary
        browser_executable = None
        for path in candidates:
            if os.path.isabs(path):
                if os.path.exists(path):
                    browser_executable = path
                    break
            elif shutil.which(path):
                browser_executable = shutil.which(path)
                break

        # Launch with Chromium flags if browser found
        if browser_executable:
            cmd = [browser_executable]

            # Use a persistent profile on desktop platforms to ensure flags are respected
            # even if Chrome is already open and to preserve cookies (user name).
            # Skip on Pi (dedicated kiosk device uses default profile).
            if not self.karaoke.is_raspberry_pi:
                cmd.append(f"--user-data-dir={self.browser_profile_dir}")

            if self.window_size:
                # Windowed mode: use --app for minimal UI, --new-window to ensure sizing works
                cmd.append("--new-window")
                cmd.append(f"--window-size={self.window_size}")
                cmd.append(f"--app={self.splash_url}")
            else:
                cmd.append("--kiosk")

            # Flags to bypass interactions and errors
            cmd.extend(
                [
                    "--autoplay-policy=no-user-gesture-required",
                    "--no-user-gesture-required",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-infobars",
                    "--disable-session-crashed-bubble",
                    "--disable-translate",
                    "--disable-restore-session-state",
                    "--disable-background-networking",
                    "--check-for-update-interval=31536000",
                    "--password-store=basic",
                ]
            )

            if self.external_monitor:
                cmd.append("--window-position=2000,0")
            else:
                cmd.append("--window-position=0,0")

            # Pi optimizations
            if self.karaoke.is_raspberry_pi:
                cmd.append("--disable-dev-shm-usage")

            # URL must be last argument for --kiosk mode
            if not self.window_size:
                cmd.append(self.splash_url)

            logging.debug(f"Browser command: {' '.join(cmd)}")
            try:
                self.browser_process = subprocess.Popen(cmd, stdout=stdout_dest, stderr=stderr_dest)
            except OSError as e:
                logging.error(f"Failed to launch browser subprocess: {e}")
        else:
            # Fallback: System default browser (without confirm=false since user can interact)
            try:
                logging.warning("No Kiosk-capable browser found. Opening system default.")
                webbrowser.open(self.splash_url, new=1, autoraise=True)
            except webbrowser.Error as e:
                logging.error(f"Error opening system browser: {e}")

    def close(self):
        """Close the browser process and all child processes."""
        if self.browser_process is not None:
            logging.info(f"Terminating browser process {self.browser_process.pid}")
            if is_windows():
                # Signal main process to close (allows Chrome to save cookies)
                subprocess.call(
                    ["taskkill", "/PID", str(self.browser_process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Give Chrome time to save cookies and shut down gracefully
                time.sleep(1)
                # Force kill any remaining child processes
                subprocess.call(
                    ["taskkill", "/F", "/T", "/PID", str(self.browser_process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                self.browser_process.terminate()

            self.browser_process.wait()
            self.browser_process = None
        else:
            logging.warning("Browser opened via system default cannot be closed by PiKaraoke.")
