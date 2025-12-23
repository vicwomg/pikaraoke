"""Selenium utilities for launching the splash screen browser."""

from __future__ import annotations

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
    karaoke: Karaoke, window_size: str | None = None
) -> webdriver.Chrome | bool:
    """Launch the Chrome browser with the splash screen in kiosk mode.

    Opens Chrome to display the karaoke splash screen with QR code
    and player interface.

    Args:
        karaoke: Karaoke instance with URL and platform configuration.
        window_size: Optional window geometry as "width,height" string.

    Returns:
        Chrome WebDriver instance on success, or False on failure.
    """
    if karaoke.is_raspberry_pi:
        service = Service(executable_path="/usr/bin/chromedriver")
    else:
        service = None
    options = Options()

    if window_size:
        options.add_argument("--window-size=%s" % (window_size))
        options.add_argument("--window-position=0,0")

    options.add_argument("--kiosk")
    options.add_argument("--start-maximized")
    options.add_argument("--autoplay-policy=no-user-gesture-required")  # Allow autoplay and seeking
    options.add_argument("--disable-features=MediaSessionService")  # Prevent media session interference
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    try:
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(f"{karaoke.url}/splash")
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
        return False
    except Exception as e:
        print(f"\n[ERROR] Error starting splash screen. See next line for output:`\n")
        print(str(e))
        return False
