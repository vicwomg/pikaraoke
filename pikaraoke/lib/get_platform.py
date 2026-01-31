"""Platform detection utilities for PiKaraoke."""

from __future__ import annotations

import io
import logging
import os
import platform
import re
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def is_raspberry_pi() -> bool:
    """Check if the current system is a Raspberry Pi.

    Returns:
        True if running on a Raspberry Pi, False otherwise.
    """
    try:
        with io.open("/sys/firmware/devicetree/base/model", "r") as m:
            if "raspberry pi" in m.read().lower():
                return True
    except Exception:
        pass
    return False


def is_android() -> bool:
    """Check if the current system is Android.

    Returns:
        True if running on Android, False otherwise.
    """
    return os.path.exists("/system/app/") and os.path.exists("/system/priv-app")


def is_windows() -> bool:
    """Check if the current system is Windows.

    Returns:
        True if running on Windows, False otherwise.
    """
    return sys.platform.startswith("win")


def is_macos() -> bool:
    """Check if the current system is macOS.

    Returns:
        True if running on macOS, False otherwise.
    """
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Check if the current system is Linux.

    Returns:
        True if running on Linux, False otherwise.
    """
    return sys.platform.startswith("linux")


def get_installed_js_runtime() -> str | None:
    """Get the name of an installed JavaScript runtime.

    Checks for deno, node, bun, and quickjs in order of preference.
    A JS runtime is required by yt-dlp for some downloads.

    Returns:
        Name of the installed runtime ('deno', 'node', 'bun', 'quickjs'),
        or None if none is installed.
    """
    if shutil.which("deno") is not None:
        return "deno"
    if shutil.which("node") is not None:
        return "node"
    if shutil.which("bun") is not None:
        return "bun"
    if shutil.which("quickjs") is not None:
        return "quickjs"
    return None


def has_js_runtime() -> bool:
    """Check if a JavaScript runtime is installed.

    Returns:
        True if a JS runtime is available, False otherwise.
    """
    return get_installed_js_runtime() is not None


def get_platform() -> str:
    """Detect the current operating system/platform.

    Returns:
        Platform identifier string: 'osx', 'android', 'linux', 'windows',
        'unknown', or the Raspberry Pi model string if on a Pi.
    """
    if is_macos():
        return "osx"
    elif is_android():
        return "android"
    elif is_raspberry_pi():
        try:
            with open("/proc/device-tree/model", "r") as file:
                model = file.read().strip()
                if "Raspberry Pi" in model:
                    return model  # Returns something like "Raspberry Pi 4 Model B Rev 1.2"
                return "Raspberry Pi - unrecognized"
        except FileNotFoundError:
            return "Raspberry Pi - unrecognized"
    elif is_linux():
        return "linux"
    elif is_windows():
        return "windows"
    else:
        return "unknown"


def get_default_dl_dir(platform: str) -> str:
    """Get the default download directory for the given platform.

    Checks for legacy directory locations and returns those if they exist,
    otherwise returns the new default location.

    Args:
        platform: Platform identifier from get_platform().

    Returns:
        Path string for the default download directory.
    """
    if is_raspberry_pi():
        return "~/pikaraoke-songs"
    elif is_windows():
        legacy_directory = os.path.expanduser("~\\pikaraoke\\songs")
        if os.path.exists(legacy_directory):
            return legacy_directory
        else:
            return "~\\pikaraoke-songs"
    else:
        legacy_directory = "~/pikaraoke/songs"
        if os.path.exists(legacy_directory):
            return legacy_directory
        else:
            return "~/pikaraoke-songs"


def get_os_version() -> str:
    """Get the operating system version string.

    Returns:
        OS version string from platform.version().
    """
    return platform.version()


def get_data_directory() -> str:
    """Get the writable data directory for the application.

    Determines the appropriate location for storing application data
    (config, logs, etc.) based on the operating system.

    Returns:
        Path to the data directory.
    """
    if is_windows():
        # Windows: %APPDATA%/pikaraoke
        base_path = os.environ.get("APPDATA")
        # Fallback if APPDATA is not set (rare, but possible)
        if not base_path:
            base_path = os.path.expanduser("~")
        path = os.path.join(base_path, "pikaraoke")
    else:
        # Linux, macOS, Android, Raspberry Pi: ~/.pikaraoke
        path = os.path.expanduser("~/.pikaraoke")

    # Ensure the directory exists
    if not os.path.exists(path):
        os.makedirs(path)

    return path


def _get_secondary_monitor_linux() -> tuple[int, int] | None:
    """Parse xrandr output for secondary monitor coordinates.

    Returns:
        (x, y) coordinates of a non-primary monitor, or None if not found.
    """
    output = subprocess.check_output(["xrandr", "--query"], text=True)
    # Pattern: " connected" followed by geometry like "1920x1080+1920+0"
    matches = re.findall(r" connected.*?(\d+)x(\d+)\+(\d+)\+(\d+)", output)

    # Return first monitor not at origin (0,0)
    for _, _, x, y in matches:
        x_coord, y_coord = int(x), int(y)
        if x_coord != 0 or y_coord != 0:
            return x_coord, y_coord

    # Fallback: return second monitor if multiple exist
    if len(matches) >= 2:
        return int(matches[1][2]), int(matches[1][3])
    return None


def _get_secondary_monitor_windows() -> tuple[int, int] | None:
    """Use Win32 API to enumerate monitor positions.

    Returns:
        (x, y) coordinates of a non-primary monitor, or None if not found.
    """
    import ctypes
    import ctypes.wintypes

    monitors: list[tuple[int, int]] = []

    def callback(_hMonitor, _hdcMonitor, lprcMonitor, _dwData):
        rect = lprcMonitor.contents
        monitors.append((rect.left, rect.top))
        return True  # Continue enumeration

    # Define callback type with correct signature
    MONITORENUMPROC = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        ctypes.c_bool,
        ctypes.c_void_p,  # hMonitor
        ctypes.c_void_p,  # hdcMonitor
        ctypes.POINTER(ctypes.wintypes.RECT),  # lprcMonitor
        ctypes.c_void_p,  # dwData (LPARAM)
    )

    # Keep reference to prevent garbage collection
    callback_func = MONITORENUMPROC(callback)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_func, 0)  # type: ignore[attr-defined]

    # Return first non-origin monitor
    for x, y in monitors:
        if x != 0 or y != 0:
            return x, y

    # Fallback: return second monitor if multiple exist
    if len(monitors) >= 2:
        return monitors[1]
    return None


def get_secondary_monitor_coords() -> tuple[int, int] | None:
    """Detect secondary monitor coordinates for splash screen positioning.

    Uses native OS tools to avoid external dependencies.
    Windows: Win32 EnumDisplayMonitors API
    Linux: xrandr (X11 only, Wayland falls back to None)
    macOS: No reliable zero-dependency method, returns None

    Returns:
        (x, y) of a non-primary monitor, or None if detection fails.
    """
    try:
        if is_linux():
            return _get_secondary_monitor_linux()
        elif is_windows():
            return _get_secondary_monitor_windows()
        # macOS: No reliable zero-dependency method, use fallback
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.debug("Monitor detection failed: %s", e)
    return None
