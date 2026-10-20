"""Raise, clear and reshape the `ytdl.broken` alert from real yt-dlp outcomes.

A search is the only proof extraction works, and an update only helps when a
newer yt-dlp exists. Centralised here so the search page and the update worker
shape one alert from facts rather than guesses.
"""

from __future__ import annotations

import os
import re

import flask_babel

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ytdlp_binary import binary_path

_ = flask_babel.gettext

# yt-dlp versions are dates (YYYY.MM.DD...). A value that does not match is the
# "Not found" / "Error" get_youtubedl_version returns when yt-dlp will not run.
_YTDLP_VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}")


def raise_ytdl_broken(events: EventSystem) -> None:
    """Searches are failing; updating yt-dlp is the likely fix on this platform."""
    if os.path.exists(binary_path()):
        # MSG: Admin alert when searches fail and yt-dlp can self-update here.
        message = _("Searches are failing. Try updating yt-dlp.")
        # MSG: Button on the yt-dlp-broken alert; jumps to the Update yt-dlp control.
        label = _("Update yt-dlp")
        url = "#system"
    else:
        # No self-updating build here (32-bit ARM, Android): the fix is a newer PiKaraoke.
        # MSG: Admin alert when searches fail and yt-dlp cannot self-update here.
        message = _("Searches are failing. yt-dlp cannot update itself on this platform.")
        # MSG: Button on the yt-dlp-broken alert linking to the PiKaraoke releases.
        label = _("How to update PiKaraoke")
        url = "https://github.com/vicwomg/pikaraoke/releases/latest"
    events.emit("alert", "ytdl.broken", "danger", message, label, url)


def raise_ytdl_current(events: EventSystem) -> None:
    """Searches fail but yt-dlp is already latest, so updating cannot help."""
    events.emit(
        "alert",
        "ytdl.broken",
        "danger",
        # MSG: Admin alert when searches fail though yt-dlp is already up to date.
        _(
            "Searches are failing, but yt-dlp is already up to date. This is likely "
            "a YouTube change yt-dlp has not fixed yet; try again later."
        ),
    )


def clear_ytdl_broken(events: EventSystem) -> None:
    events.emit("alert_clear", "ytdl.broken")


def reconcile_after_update(events: EventSystem, before: str, after: str) -> None:
    """Reshape `ytdl.broken` from an update attempt's version change.

    Call only when the alert is active. A changed version means a newer yt-dlp
    went in (the next search re-proves it); an unchanged one means there was
    nothing to install, so updating cannot fix the failure.
    """
    if not _YTDLP_VERSION.match(after):
        return  # the update produced no usable version; leave the alert, the log says why
    if after != before:
        clear_ytdl_broken(events)
    else:
        raise_ytdl_current(events)
