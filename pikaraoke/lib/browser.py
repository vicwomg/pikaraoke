import enum
import logging
import platform
import subprocess

logger = logging.getLogger(__name__)


class Browser(enum.Enum):
    FIREFOX = "firefox"
    CHROME = "chrome"
    EDGE = "edge"
    SAFARI = "safari"
    UNSUPPORTED = "unsupported"


def _get_default_browser_linux() -> str:
    try:
        result = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception as e:
        logger.error(f"Failed to get default browser on Linux: {e}")

    return ""


def _get_default_browser_macos() -> str:
    import plistlib

    try:
        result = subprocess.run(
            [
                "defaults",
                "read",
                "com.apple.LaunchServices/com.apple.launchservices.secure",
                "LSHandlers",
            ],
            capture_output=True,
            text=True,
        )
        handlers: list[dict] = plistlib.loads(result.stdout.encode())
        for handler in handlers:
            if handler.get("LSHandlerURLScheme") == "http":
                return handler.get("LSHandlerRoleAll", "")
    except Exception as e:
        logger.error(f"Failed to get default browser on macOS: {e}")

    return ""


def _get_default_browser_windows() -> str:
    import winreg

    key_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
            return str(prog_id)
    except Exception as e:
        logger.error(f"Failed to get default browser on Windows: {e}")

    return ""


def get_default_browser() -> Browser:
    """Reads the default browser from the OS

    Returns:
        Browser: Browser type
    """
    os_type = platform.system()
    logger.debug(f"Recognized OS: {os_type=}")

    try:
        if os_type == "Windows":
            browser_name = _get_default_browser_windows()
        elif os_type == "Darwin":  # macOS
            browser_name = _get_default_browser_macos()
        elif os_type == "Linux":
            browser_name = _get_default_browser_linux()
        else:
            browser_name = "unsupported"
    except Exception as e:
        logger.error(e)
        browser_name = ""

    if Browser.FIREFOX.value in browser_name:
        return Browser.FIREFOX
    elif Browser.CHROME.value in browser_name or "chromium" in browser_name:
        return Browser.CHROME
    elif Browser.SAFARI.value in browser_name:
        return Browser.SAFARI
    elif Browser.EDGE.value in browser_name:
        return Browser.EDGE
    else:
        return Browser.UNSUPPORTED


if __name__ == "__main__":
    print("Default browser:", get_default_browser())
