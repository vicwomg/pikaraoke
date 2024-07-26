import os
import sys
from enum import Enum


class Platform(Enum):
    """Which OS the current host is among OSX, RPI, LINUX, WINDOWS, UNKNOWN.

    Supports methods: `is_rpi()` `is_windows()` `is_linux()` `is_mac()`.

    ### Example:

    ```
    platform = Platform.LINUX
    platform.is_linux() # Returns True
    platform.is_max() # Returns False
    ```
    """

    OSX = "osx"
    RASPBERRY_PI = "raspberry_pi"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"

    def is_rpi(self):
        return self == Platform.RASPBERRY_PI

    def is_windows(self):
        return self == Platform.WINDOWS

    def is_linux(self):
        return self == Platform.LINUX

    def is_mac(self):
        return self == Platform.OSX


def _is_raspberry_pi() -> bool:
    try:
        return (
            os.uname()[4][:3] == "arm" or os.uname()[4] == "aarch64"
        ) and sys.platform != "darwin"
    except AttributeError:
        return False


def get_platform() -> Platform:
    if "darwin" in sys.platform:
        return Platform.OSX
    elif _is_raspberry_pi():
        return Platform.RASPBERRY_PI
    elif sys.platform.startswith(Platform.LINUX.value):
        return Platform.LINUX
    elif sys.platform.startswith("win"):
        return Platform.WINDOWS
    else:
        return Platform.UNKNOWN
