"""Network utilities for PiKaraoke."""

import logging
import socket
import subprocess


def get_ip(platform: str) -> str:
    """Get the local IP address of this machine.

    Uses platform-specific methods to reliably determine the local IP address,
    handling edge cases like multiple network adapters on Windows.

    Args:
        platform: Platform identifier from get_platform() (e.g., 'windows', 'android', 'linux').

    Returns:
        IP address string, or '127.0.0.1' if detection fails.
    """
    if platform == "android":
        return _get_ip_android()
    elif platform == "windows":
        return _get_ip_windows()
    else:
        return _get_ip_default()


def _get_ip_android() -> str:
    """Get IP address on Android using ifconfig.

    Returns:
        IP address string.
    """
    # python socket.connect will not work on android, access denied.
    # Workaround: use ifconfig which is installed to termux by default.
    try:
        ip = (
            subprocess.check_output(
                "ifconfig 2> /dev/null | awk '/wlan0/{flag=1} flag && /inet /{print $2; exit}'",
                shell=True,
            )
            .decode("utf8")
            .strip()
        )
        return ip if ip else "127.0.0.1"
    except Exception as e:
        logging.warning(f"Could not determine IP address on Android: {e}")
        return "127.0.0.1"


def _get_ip_windows() -> str:
    """Get IP address on Windows.

    On Windows, the UDP socket trick can return wrong IPs when multiple network
    adapters exist (VPN, Hyper-V, WSL, Docker, etc.). This uses socket.gethostbyname
    as the primary method with UDP socket as fallback.

    Returns:
        IP address string.
    """
    ip = "127.0.0.1"
    try:
        # Try getting IP from hostname first
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)

        # If we got localhost, fall back to the UDP socket trick
        if ip.startswith("127."):
            ip = _get_ip_via_udp_socket("8.8.8.8")
    except Exception as e:
        logging.warning(f"Could not determine IP address on Windows: {e}")
        ip = "127.0.0.1"

    return ip


def _get_ip_default() -> str:
    """Get IP address using UDP socket trick (Linux, macOS, etc.).

    This method is reliable on most Unix-like systems.
    Reference: https://stackoverflow.com/a/28950774

    Returns:
        IP address string.
    """
    return _get_ip_via_udp_socket("10.255.255.255")


def _get_ip_via_udp_socket(target: str) -> str:
    """Get local IP by creating a UDP socket to a target address.

    Creates a UDP socket and "connects" to a target (no actual packets sent).
    This causes the OS to select the appropriate source IP for routing.

    Args:
        target: Target IP address to "connect" to.

    Returns:
        IP address string, or '127.0.0.1' if detection fails.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect((target, 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip
