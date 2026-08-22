"""Startup security checks for PiKaraoke."""

import ipaddress
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _is_public_ip(ip: str) -> bool:
    """Determine if an IP address is public (internet routable).

    Args:
        ip: IP address string.

    Returns:
        True if the IP is public, False if it is private/reserved.
    """
    try:
        addr = ipaddress.ip_address(ip)
        return not addr.is_private and not addr.is_loopback and not addr.is_reserved
    except ValueError:
        return False


def check_internet_exposure(port: int, bind_address: Optional[str] = None) -> bool:
    """Check if PiKaraoke appears to be internet exposed.

    Args:
        port: The port PiKaraoke is listening on.
        bind_address: The address PiKaraoke is bound to (None = all interfaces).

    Returns:
        True if likely internet exposed, False otherwise.
    """
    if bind_address is None or bind_address == "0.0.0.0" or bind_address == "::":
        return True

    return _is_public_ip(bind_address)


def warn_if_internet_exposed(port: int, url: Optional[str] = None) -> None:
    """Log a warning if PiKaraoke appears internet exposed.

    Args:
        port: The port PiKaraoke is listening on.
        url: The full URL being served (e.g., "http://192.168.1.5:5555").
    """
    if not url:
        return

    if url.startswith("http://") or url.startswith("https://"):
        try:
            host_part = url.split("://")[1].split(":")[0]
            if _is_public_ip(host_part):
                logger.warning(
                    "SECURITY WARNING: PiKaraoke appears to be internet exposed. "
                    "A public IP address detected. Ensure proper network controls are in place. "
                    "Recommend: use a VPN, run behind a reverse proxy with authentication, or restrict network access."
                )
                return
        except (IndexError, ValueError):
            pass

    logger.debug("PiKaraoke startup: no public IP detected, appears to be LAN-only")
