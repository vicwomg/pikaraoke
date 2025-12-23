import io
import os
import platform
import shutil
import sys


def is_raspberry_pi():
    try:
        with io.open("/sys/firmware/devicetree/base/model", "r") as m:
            if "raspberry pi" in m.read().lower():
                return True
    except Exception:
        pass
    return False


def is_android():
    return os.path.exists("/system/app/") and os.path.exists("/system/priv-app")


def get_installed_js_runtime():
    # prioritize deno and node
    if shutil.which("deno") is not None:
        return "deno"
    if shutil.which("node") is not None:
        return "node"
    if shutil.which("bun") is not None:
        return "bun"
    if shutil.which("quickjs") is not None:
        return "quickjs"
    return None


def has_js_runtime():
    return get_installed_js_runtime() is not None


def get_platform():
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
        except FileNotFoundError:
            return "Rasperry Pi - unrecognized"
    elif sys.platform.startswith("linux"):
        return "linux"
    elif sys.platform.startswith("win"):
        return "windows"
    else:
        return "unknown"


def should_use_mp4_streaming() -> bool:
    """
    Determines if MP4 streaming should be used instead of HLS.
    Returns True for older Raspberry Pi models (3B+, 3B, or earlier)
    where Chromium doesn't support HLS natively.
    """
    if not is_raspberry_pi():
        return False

    try:
        with open("/proc/device-tree/model", "r") as file:
            model = file.read().strip().lower()
            # Detect RPi 3B+, 3B, or earlier (not 4 or 5)
            if "raspberry pi 3" in model or "raspberry pi 2" in model or "raspberry pi 1" in model or "raspberry pi zero" in model:
                return True
    except FileNotFoundError:
        pass

    return False


def get_default_dl_dir(platform: str) -> str:
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


def get_os_version():
    return platform.version()
