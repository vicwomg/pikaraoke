"""Browser utilities for launching the splash screen."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from typing import TYPE_CHECKING

from pikaraoke.lib.get_platform import get_data_directory

if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


def get_browser_profile_dir() -> str:
    """Get the persistent browser profile directory for kiosk mode.

    Returns:
        Path to the browser profile directory within the app's data directory.
    """
    return os.path.join(get_data_directory(), "browser_profile")


def launch_splash_screen(
    karaoke: Karaoke,
    window_size: str | None = None,
    external_monitor: bool = False,
) -> subprocess.Popen | None:
    """Launch the browser with the splash screen in kiosk mode.

    Uses a persistent user data directory to ensure kiosk flags are respected
    even if another browser instance is running, and to preserve cookies
    (such as user name) across restarts. Supports Chrome, Chromium, and Edge
    on Windows, Linux, and macOS.

    Args:
        karaoke: Karaoke instance with URL and platform configuration.
        window_size: Optional window geometry as "width,height" string.
        external_monitor: If True, position window on external monitor.

    Returns:
        Popen process handle on success, or None on failure.
    """
    # confirm=false tells the splash page to hide the modal automatically
    karaoke_url = f"{karaoke.url}/splash?confirm=false"
    logging.info(f"Launching splash screen: {karaoke_url}")

    suppress_logs = int(karaoke.log_level) > logging.DEBUG
    stdout_dest = subprocess.DEVNULL if suppress_logs else None
    stderr_dest = subprocess.DEVNULL if suppress_logs else None

    # Browser candidates ordered by preference (Chrome/Chromium/Edge only)
    candidates = []

    if sys.platform == "win32":
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
    elif sys.platform.startswith("linux"):
        candidates.extend(
            [
                "chromium-browser",
                "chromium",
                "google-chrome",
                "chrome",
            ]
        )
    elif sys.platform == "darwin":
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

        if window_size:
            cmd.append(f"--window-size={window_size}")
            cmd.append(f"--app={karaoke_url}")
        else:
            cmd.append("--kiosk")
            # Use a persistent profile on desktop platforms to ensure kiosk mode works
            # even if Chrome is already open and to preserve cookies (user name).
            # Skip on Pi (dedicated kiosk device uses default profile).
            if not karaoke.is_raspberry_pi:
                cmd.append(f"--user-data-dir={get_browser_profile_dir()}")

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

        if external_monitor:
            cmd.append("--window-position=2000,0")
        else:
            cmd.append("--window-position=0,0")

        # Pi optimizations
        if karaoke.is_raspberry_pi:
            cmd.append("--disable-dev-shm-usage")

        # URL must be last argument for --kiosk mode
        if not window_size:
            cmd.append(karaoke_url)

        logging.info(f"Browser command: {' '.join(cmd)}")
        try:
            return subprocess.Popen(cmd, stdout=stdout_dest, stderr=stderr_dest)
        except OSError as e:
            logging.error(f"Failed to launch browser subprocess: {e}")

    # Fallback: System default browser
    try:
        logging.warning("No Kiosk-capable browser found. Opening system default.")
        webbrowser.open(karaoke_url, new=1, autoraise=True)
        return None
    except webbrowser.Error as e:
        logging.error(f"Error opening system browser: {e}")
        return None
