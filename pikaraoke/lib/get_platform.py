import os
import platform
import re
import subprocess
import sys


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


def is_raspberry_pi():
    try:
        return (
            os.uname()[4][:3] == "arm" or os.uname()[4] == "aarch64"
        ) and sys.platform != "darwin"
    except AttributeError:
        return False


def get_platform():
    if sys.platform == "darwin":
        return "osx"
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


def get_os_version():
    return platform.version()


def supports_hardware_h264_encoding():
    if is_raspberry_pi():
        platform = get_platform()

        # For other platform(OrangePI etc)
        if platform is None:
            return False

        # Raspberry Pi >= 5 no longer has hardware GPU decoding
        match = re.search(r"Raspberry Pi (\d+)", platform)
        if match:
            model_number = int(match.group(1))
            if model_number >= 5:
                return False
        return True
    else:
        return False
