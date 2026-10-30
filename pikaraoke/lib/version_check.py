"""Periodic check for a newer PiKaraoke release, raised as `version.available`.

The only producer needing a clock, so it runs on a greenlet off the request
path. Never self-upgrades: the alert carries the command or link to run by hand.
"""

from __future__ import annotations

import logging
import os

import flask_babel
import gevent

from pikaraoke import VERSION
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.get_platform import is_running_in_docker, is_windows

_ = flask_babel.gettext

_RELEASES_API = "https://api.github.com/repos/vicwomg/pikaraoke/releases/latest"
_RELEASES_PAGE = "https://github.com/vicwomg/pikaraoke/releases/latest"

# Daily: releases arrive no faster, and this sits far under the unauthenticated
# rate limit. First check delayed past the cold-start library scan.
_FIRST_CHECK_DELAY_SECONDS = 300
_INTERVAL_SECONDS = 24 * 60 * 60

# Dev seam: substitutes "latest", skipping the lookup and the delay. Not a setting.
_FAKE_LATEST_ENV = "PIKARAOKE_FAKE_LATEST_VERSION"


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Parse a release tag to a comparable tuple; None (never raises) if it is not one."""
    parts = text.strip().lstrip("vV").split(".")
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            break
        numbers.append(int(part))
    return tuple(numbers) or None


def _is_newer(latest: str, current: str) -> bool:
    latest_parsed = _parse_version(latest)
    current_parsed = _parse_version(current)
    if latest_parsed is None or current_parsed is None:
        return False
    return latest_parsed > current_parsed


def _fetch_latest_version() -> str | None:
    """The latest published release tag, or None if unreachable. Never raises."""
    # Lazy so this module never pulls requests in ahead of gevent's monkey-patching.
    import requests

    try:
        resp = requests.get(_RELEASES_API, timeout=10)
        resp.raise_for_status()
        tag = resp.json().get("tag_name")
        return tag if isinstance(tag, str) and tag else None
    except (requests.RequestException, ValueError) as e:
        logging.debug(f"PiKaraoke version check could not reach the release API: {e}")
        return None


def _upgrade_action(latest: str) -> tuple[str, str | None]:
    """How to upgrade here: (label, url); a None url marks the label a command to copy."""
    if is_windows():
        # MSG: Button on the update alert that opens the PiKaraoke download page.
        return _("Download the update"), _RELEASES_PAGE
    if is_running_in_docker():
        # uv tool upgrade in a container vanishes on recreation, so it is worse than nothing.
        return "docker compose pull && docker compose up -d", None
    return "uv tool upgrade pikaraoke", None


def check_for_update(events: EventSystem) -> None:
    """Raise `version.available` when a newer release exists; nothing otherwise."""
    latest = os.environ.get(_FAKE_LATEST_ENV) or _fetch_latest_version()
    if not latest or not _is_newer(latest, VERSION):
        return

    label, url = _upgrade_action(latest)
    # MSG: Admin alert announcing a newer PiKaraoke release. %(version)s is the release number.
    message = _("PiKaraoke %(version)s is available") % {"version": latest}
    events.emit("alert", "version.available", "warning", message, label, url)


def run_update_checks(events: EventSystem) -> None:
    """Greenlet loop: at startup (after a delay) then every 24 hours. Dies with the process."""
    if not os.environ.get(_FAKE_LATEST_ENV):
        gevent.sleep(_FIRST_CHECK_DELAY_SECONDS)
    while True:
        check_for_update(events)
        gevent.sleep(_INTERVAL_SECONDS)
