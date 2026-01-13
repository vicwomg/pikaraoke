"""Selenium utilities for launching the splash screen browser."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


def launch_splash_screen(
    karaoke: Karaoke,
    window_size: str | None = None,
    external_monitor: bool = False,
) -> webdriver.Chrome | None:
    """Launch the Chrome browser with the splash screen in kiosk mode.

    Opens Chrome to display the karaoke splash screen with QR code
    and player interface.

    Args:
        karaoke: Karaoke instance with URL and platform configuration.
        window_size: Optional window geometry as "width,height" string.
        external_monitor: If True, position window on external monitor (x=1920).

    Returns:
        Chrome WebDriver instance on success, or None on failure.
    """
    # Determine if we should suppress logs based on configured log level
    # If log level is DEBUG (10) or lower, we want to see selenium logs for debugging.
    # We cast to int because the argument parser might store it as a string or int.
    suppress_logs = int(karaoke.log_level) > logging.DEBUG

    log_output = subprocess.DEVNULL if suppress_logs else None

    if karaoke.is_raspberry_pi:
        service = Service(executable_path="/usr/bin/chromedriver", log_output=log_output)
    else:
        service = Service(log_output=log_output)
    options = Options()

    karaoke_url = f"{karaoke.url}/splash"

    if window_size:
        options.add_argument("--window-size=%s" % (window_size))
        # Option to hide URL bar and title bars for a cleaner UI
        # Hide URL bar: Use --app to open in minimal UI mode (removes tabs/url/title bar)
        # Note: --app disables kiosk, so only use if window_size is set (not fullscreen kiosk mode)
        # If window_size is set, we assume the user wants windowed mode, and hiding chrome is okay
        options.add_argument("--app=%s" % karaoke_url)
    else:
        options.add_argument("--kiosk")

    if external_monitor:
        options.add_argument("--window-position=2000,0")
    else:
        options.add_argument("--window-position=0,0")

    options.add_argument("--disable-infobars")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Suppress Chrome console warnings (GCM, TensorFlow, etc.) only if not debugging
    if suppress_logs:
        options.add_argument("--log-level=3")  # Only show fatal errors

    # Raspberry Pi specific optimizations for resource-constrained hardware
    if karaoke.is_raspberry_pi:
        # Memory management - critical for Pi's 1GB RAM
        options.add_argument("--disable-dev-shm-usage")  # Don't use /dev/shm (limited to 50-100MB)
        options.add_argument(
            "--disable-features=VizDisplayCompositor"
        )  # Reduce GPU compositor overhead

        # GPU optimization - free GPU memory for video decode and h264_v4l2m2m encoder
        options.add_argument("--disable-gpu-compositing")  # Use CPU for UI, GPU for video only
        options.add_argument(
            "--disable-software-rasterizer"
        )  # Force GPU rendering, no CPU fallback

        # Performance tuning - reduce overhead on limited CPU
        options.add_argument("--disable-gpu-vsync")  # Don't wait for vsync, reduces GPU load
        options.add_argument("--disable-background-timer-throttling")  # Keep SocketIO responsive
        options.add_argument(
            "--disable-backgrounding-occluded-windows"
        )  # Don't suspend kiosk window

        # Media optimization
        options.add_argument("--autoplay-policy=no-user-gesture-required")  # Enable autoplay
        options.add_argument("--use-gl=egl")  # Use EGL (Embedded GL) instead of desktop GL

    try:
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(f"{karaoke_url}")
        driver.add_cookie({"name": "user", "value": "PiKaraoke-Host"})
        # Clicking this counts as an interaction, which will allow the browser to autoplay audio
        wait = WebDriverWait(driver, 60)
        elem = wait.until(EC.element_to_be_clickable((By.ID, "permissions-button")))
        elem.click()
        return driver
    except SessionNotCreatedException as e:
        print(str(e))
        print(
            f"\n[ERROR] Error starting splash screen. If you're running headed mode over SSH, you may need to run `export DISPLAY=:0.0` first to target the host machine's screen. Example: `export DISPLAY=:0.0; pikaraoke`\n"
        )
        return None
    except Exception as e:
        print(f"\n[ERROR] Error starting splash screen. See next line for output:`\n")
        print(str(e))
        return None
