import shutil
import subprocess

import flask_babel
from flask import Blueprint, render_template

from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.raspi_wifi_config import get_raspi_wifi_text

_ = flask_babel.gettext


splash_bp = Blueprint("splash", __name__)


@splash_bp.route("/splash")
def splash():
    k = get_karaoke_instance()
    text = ""
    if k.is_raspberry_pi:
        has_iwconfig = shutil.which("iwconfig")
        has_iw = shutil.which("iw")
        if has_iwconfig or has_iw:
            # iwconfig is deprecated on Ubuntu, but still available on Raspbian
            command = "iwconfig" if has_iwconfig else "iw"
            status = subprocess.run([command, "wlan0"], stdout=subprocess.PIPE).stdout.decode(
                "utf-8"
            )
            if "Mode:Master" in status:
                # handle raspiwifi connection mode
                text = get_raspi_wifi_text()

    return render_template(
        "splash.html",
        blank_page=True,
        url=k.url,
        hostap_info=text,
        hide_url=k.hide_url,
        hide_overlay=k.hide_overlay,
        screensaver_timeout=k.screensaver_timeout,
        disable_bg_music=k.disable_bg_music,
        disable_bg_video=k.disable_bg_video,
        disable_score=k.disable_score,
        bg_music_volume=k.bg_music_volume,
        has_bg_video=k.bg_video_path is not None,
    )
