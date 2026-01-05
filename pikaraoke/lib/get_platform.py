"""Platform detection utilities for PiKaraoke."""

import io
import os
import platform
import shutil
import sys


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
    if sys.platform == "darwin":
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
    elif sys.platform.startswith("linux"):
        return "linux"
    elif sys.platform.startswith("win"):
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
    elif platform == "windows":
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
