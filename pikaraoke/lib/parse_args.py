import argparse
import importlib.resources as pkg_resources
import logging
import os
import sys
from pathlib import Path

from pikaraoke import resources

from .get_platform import Platform, get_platform

logger = logging.getLogger(__name__)


def get_default_dl_dir(platform: Platform) -> Path:
    default_dir = Path.home() / "pikaraoke-songs"
    legacy_dir = Path.home() / "pikaraoke" / "songs"

    if not platform.is_rpi() and legacy_dir.exists():
        return legacy_dir

    return default_dir


PORT = 5555
PORT_FFMPEG = 5556
VOLUME = 0.85
DELAY_SPLASH = 3
DELAY_SCREENSAVER = 300
LOG_LEVEL = logging.INFO
PREFER_HOSTNAME = False
PLATFORM = get_platform()
DL_DIR: Path = get_default_dl_dir(PLATFORM)


def volume_type(input):
    """Verify the volume input"""
    try:
        volume = float(input)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Volume must be a float between 0 and 1, but got '{input}'"
        )

    if volume < 0 or volume > 1:
        raise argparse.ArgumentTypeError(f"Volume must be between 0 and 1, but got {volume}")

    return volume


class ArgsNamespace(argparse.Namespace):
    """Provides typehints to the input args"""

    port: int
    window_size: str
    ffmpeg_port: int
    download_path: Path
    volume: float
    splash_delay: float
    screensaver_timeout: float
    log_level: int
    hide_url: bool
    prefer_hostname: bool
    hide_raspiwifi_instructions: bool
    hide_splash_screen: bool
    high_quality: bool
    logo_path: Path
    url: str | None
    ffmpeg_url: str | None
    hide_overlay: bool
    admin_password: str | None


def _get_logo_path():
    try:
        # Access the resource using importlib.resources
        return pkg_resources.path(resources, "logo.png")
        # Resolve the path to an actual file
    except Exception as e:
        print(f"Error accessing logo.png: {e}")
        return None


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def parse_args() -> ArgsNamespace:
    # Usage example to get path to logo.png inside the executable
    # logo_path_default = resource_path("resources/logo.png") # Works in pyinstaller
    logo_path_default = _get_logo_path()  # Works in poetry
    logger.debug(f"{logo_path_default=}")
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-p",
        "--port",
        help="Desired http port (default: %d)" % PORT,
        default=PORT,
        required=False,
    )
    parser.add_argument(
        "--window-size",
        help="Desired window geometry in pixels, specified as width,height",
        default=0,
        required=False,
    )
    parser.add_argument(
        "-f",
        "--ffmpeg-port",
        help=f"Desired ffmpeg port. This is where video stream URLs will be pointed (default: {PORT_FFMPEG})",
        default=PORT_FFMPEG,
        required=False,
    )
    parser.add_argument(
        "-d",
        "--download-path",
        help=f"Desired path for downloaded songs. Defaults to {DL_DIR}",
        default=DL_DIR,
        type=Path,
    )
    parser.add_argument(
        "-y",
        "--youtubedl-path",
        help=f"(DEPRECATED!) Path to yt-dlp binary. Defaults to None.",
        default=None,
        type=Path,
        required=False,
    )

    parser.add_argument(
        "-v",
        "--volume",
        help="Set initial player volume. A value between 0 and 1. (default: %s)" % VOLUME,
        default=VOLUME,
        type=volume_type,
        required=False,
    )
    parser.add_argument(
        "-s",
        "--splash-delay",
        help="Delay during splash screen between songs (in secs). (default: %s )" % DELAY_SPLASH,
        default=DELAY_SPLASH,
        required=False,
    )
    parser.add_argument(
        "-t",
        "--screensaver-timeout",
        help="Delay before the screensaver begins (in secs). (default: %s )" % DELAY_SCREENSAVER,
        default=DELAY_SCREENSAVER,
        required=False,
    )
    parser.add_argument(
        "-l",
        "--log-level",
        help=f"Logging level int value (DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50). (default: {LOG_LEVEL} )",
        default=LOG_LEVEL,
        required=False,
    )
    parser.add_argument(
        "--hide-url",
        action="store_true",
        help="Hide URL and QR code from the splash screen.",
        required=False,
    )
    parser.add_argument(
        "--prefer-hostname",
        action="store_true",
        help=f"Use the local hostname instead of the IP as the connection URL. Use at your discretion: mDNS is not guaranteed to work on all LAN configurations. Defaults to {PREFER_HOSTNAME}",
        default=PREFER_HOSTNAME,
        required=False,
    )
    parser.add_argument(
        "--hide-raspiwifi-instructions",
        action="store_true",
        help="Hide RaspiWiFi setup instructions from the splash screen.",
        required=False,
    )
    parser.add_argument(
        "--hide-splash-screen",
        "--headless",
        action="store_true",
        help="Headless mode. Don't launch the splash screen/player on the pikaraoke server",
        required=False,
    )
    parser.add_argument(
        "--high-quality",
        action="store_true",
        help="Download higher quality video. Note: requires ffmpeg and may cause CPU, download speed, and other performance issues",
        required=False,
    )

    parser.add_argument(
        "--logo-path",
        help="Path to a custom logo image file for the splash screen. Recommended dimensions ~ 2048x1024px",
        default=logo_path_default,
        type=Path,
    )

    parser.add_argument(
        "-u",
        "--url",
        help="Override the displayed IP address with a supplied URL. This argument should include port, if necessary",
        default=None,
        required=False,
    )

    parser.add_argument(
        "-m",
        "--ffmpeg-url",
        help="Override the ffmpeg address with a supplied URL.",
        default=None,
        required=False,
    )

    parser.add_argument(
        "--hide-overlay",
        action="store_true",
        help="Hide overlay that shows on top of video with pikaraoke QR code and IP",
        required=False,
    )

    parser.add_argument(
        "--admin-password",
        help="Administrator password, for locking down certain features of the web UI such as queue editing, player controls, song editing, and system shutdown. If unspecified, everyone is an admin.",
        default=None,
        required=False,
    )

    return parser.parse_args(namespace=ArgsNamespace())
