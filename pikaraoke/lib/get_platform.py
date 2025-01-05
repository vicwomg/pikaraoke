import io
import os
import platform
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


def get_default_dl_dir(platform):
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
