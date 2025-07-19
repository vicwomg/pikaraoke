from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def launch_splash_screen(karaoke, window_size=None):
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
