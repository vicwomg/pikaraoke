import flask_babel
import psutil
from flask import Blueprint, render_template

from pikaraoke import VERSION
from pikaraoke.lib.current_app import (
    get_admin_password,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)
from pikaraoke.lib.get_platform import get_platform

_ = flask_babel.gettext


info_bp = Blueprint("info", __name__)


@info_bp.route("/info")
def info():
    k = get_karaoke_instance()
    site_name = get_site_name()
    url = k.url
    admin_password = get_admin_password()
    is_linux = get_platform() == "linux"

    # cpu
    try:
        cpu = str(psutil.cpu_percent()) + "%"
    except:
        cpu = _("CPU usage query unsupported")

    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available / 1024.0 / 1024.0, 1)
    total = round(memory.total / 1024.0 / 1024.0, 1)
    memory = (
        str(available) + "MB free / " + str(total) + "MB total ( " + str(memory.percent) + "% )"
    )

    # disk
    disk = psutil.disk_usage("/")
    # Divide from Bytes -> KB -> MB -> GB
    free = round(disk.free / 1024.0 / 1024.0 / 1024.0, 1)
    total = round(disk.total / 1024.0 / 1024.0 / 1024.0, 1)
    disk = str(free) + "GB free / " + str(total) + "GB total ( " + str(disk.percent) + "% )"

    # youtube-dl
    youtubedl_version = k.youtubedl_version

    return render_template(
        "info.html",
        site_title=site_name,
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        ffmpeg_version=k.ffmpeg_version,
        is_transpose_enabled=k.is_transpose_enabled,
        youtubedl_version=youtubedl_version,
        platform=k.platform,
        os_version=k.os_version,
        is_pi=k.is_raspberry_pi,
        is_linux=is_linux,
        pikaraoke_version=VERSION,
        admin=is_admin(),
        admin_enabled=admin_password != None,
        disable_bg_music=k.disable_bg_music,
        bg_music_volume=int(100 * k.bg_music_volume),
        disable_bg_video=k.disable_bg_video,
        disable_score=k.disable_score,
        hide_url=k.hide_url,
        limit_user_songs_by=k.limit_user_songs_by,
        avsync=k.avsync,
        hide_notifications=k.hide_notifications,
        hide_overlay=k.hide_overlay,
        normalize_audio=k.normalize_audio,
        complete_transcode_before_play=k.complete_transcode_before_play,
        high_quality=k.high_quality,
        splash_delay=k.splash_delay,
        screensaver_timeout=k.screensaver_timeout,
        volume=int(100 * k.volume),
        buffer_size=k.buffer_size,
    )
