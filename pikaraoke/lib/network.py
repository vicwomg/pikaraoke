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
    try:
        return _get_ip_via_psutil()
    except Exception as e:
        logging.warning(f"psutil method failed: {e}, using platform-specific fallback")
        # Fall back to platform-specific methods
        if platform == "android":
            return _get_ip_android()
        elif platform == "windows":
            return _get_ip_windows()
        else:
            return _get_ip_default()


def _get_ip_via_psutil() -> str:
    """Get IP address using psutil (cross-platform, filters virtual adapters).

    This method works consistently across Windows, Linux, macOS, Raspberry Pi,
    and Android by enumerating network interfaces and filtering out virtual adapters.

    Returns:
        IP address string.

    Raises:
        Exception: If psutil is not available or no suitable interface found.
    """
    import psutil  # Import here to allow graceful fallback if not available

    virtual_prefixes = (
        "lo",
        "veth",
        "docker",
        "vmnet",
        "vEthernet",
        "VirtualBox",
        "WSL",
        "Loopback",
        "utun",
        "awdl",
        "bridge",
    )

    interfaces = psutil.net_if_addrs()
    interface_stats = psutil.net_if_stats()
    candidates = []

    for interface_name, addrs in interfaces.items():
        # Skip virtual/loopback interfaces
        if interface_name.startswith(virtual_prefixes):
            continue

        # Check if interface is up
        if interface_name in interface_stats and not interface_stats[interface_name].isup:
            continue

        for addr in addrs:
            if addr.family == socket.AF_INET:  # IPv4
                ip = addr.address

                # Skip localhost and APIPA addresses
                if ip.startswith(("127.", "169.254.")):
                    continue

                # Prioritize: Ethernet > WiFi > Others
                priority = 0
                interface_lower = interface_name.lower()
                if "eth" in interface_lower or "en" in interface_lower:
                    priority = 3
                elif (
                    "wlan" in interface_lower
                    or "wi-fi" in interface_lower
                    or "wifi" in interface_lower
                ):
                    priority = 2
                else:
                    priority = 1

                candidates.append((priority, ip, interface_name))

    if candidates:
        candidates.sort(reverse=True, key=lambda x: x[0])
        selected_ip = candidates[0][1]
        selected_interface = candidates[0][2]
        logging.debug(f"Selected network interface: {selected_interface} with IP: {selected_ip}")
        return selected_ip

    raise Exception("No suitable network interface found")


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
