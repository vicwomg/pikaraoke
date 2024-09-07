import os
import platform
import re
import subprocess
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
    RPI = "rpi"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"

    def is_rpi(self):
        """Check if the platform is Raspberry Pi

        Returns:
            bool: True if the platform is Raspberry Pi, False otherwise
        """
        return self == Platform.RPI

    def is_windows(self):
        """Check if the platform is Windows

        Returns:
            bool: True if the platform is Windows, False otherwise
        """
        return self == Platform.WINDOWS

    def is_linux(self):
        """Check if the platform is Linux

        Returns:
            bool: True if the platform is Linux, False otherwise
        """
        return self == Platform.LINUX

    def is_mac(self):
        """Check if the platform is macOS

        Returns:
            bool: True if the platform is macOS, False otherwise
        """
        return self == Platform.OSX

    def is_unknown(self):
        """Check if the platform is unknown

        Returns:
            bool: True if the platform is unknown, False otherwise
        """
        return self == Platform.UNKNOWN


def get_platform() -> Platform:
    """Determine the current platform

    Returns:
        Platform: The current platform as a member of the Platform enum
    """
    if "darwin" in sys.platform:
        return Platform.OSX
    elif _is_raspberry_pi():
        return Platform.RPI
    elif sys.platform.startswith(Platform.LINUX.value):
        return Platform.LINUX
    elif sys.platform.startswith("win"):
        return Platform.WINDOWS
    else:
        return Platform.UNKNOWN


def get_ffmpeg_version():
    try:
        # Execute the command 'ffmpeg -version'
        result = subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        # Parse the first line to get the version
        first_line = result.stdout.split("\n")[0]
        version_info = first_line.split(" ")[2]  # Assumes the version info is the third element
        return version_info
    except FileNotFoundError:
        return "FFmpeg is not installed"
    except IndexError:
        return "Unable to parse FFmpeg version"


def get_os_version():
    return platform.version()


def supports_hardware_h264_encoding():
    if _is_raspberry_pi():
        platform = get_platform()

        # Raspberry Pi >= 5 no longer has hardware GPU decoding
        match = re.search(r"Raspberry Pi (\d+)", platform)
        if match:
            model_number = int(match.group(1))
            if model_number >= 5:
                return False
        return True
    else:
        return False


def _is_raspberry_pi() -> bool:
    try:
        return (
            os.uname()[4][:3] == "arm" or os.uname()[4] == "aarch64"
        ) and sys.platform != "darwin"
    except AttributeError:
        return False
