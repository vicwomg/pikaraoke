import flask_babel
import psutil
from flask import Blueprint, render_template

from pikaraoke import VERSION
from pikaraoke.constants import LANGUAGES
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

    # 獲取當前偏好語言
    preferred_language = k.get_user_preference("preferred_language", "en")

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
        admin=is_admin(),
        admin_password=admin_password,
        platform=k.platform,
        os_version=k.os_version,
        ffmpeg_version=k.ffmpeg_version,
        is_transpose_enabled=k.is_transpose_enabled,
        youtubedl_version=youtubedl_version,
        pikaraoke_version=VERSION,
        cpu=cpu,
        memory=memory,
        disk=disk,
        is_linux=is_linux,
        volume=int(k.volume * 100),
        bg_music_volume=int(k.bg_music_volume * 100),
        disable_bg_music=k.disable_bg_music,
        disable_bg_video=k.disable_bg_video,
        disable_score=k.disable_score,
        hide_notifications=k.hide_notifications,
        hide_url=k.hide_url,
        hide_overlay=k.hide_overlay,
        screensaver_timeout=k.screensaver_timeout,
        splash_delay=k.splash_delay,
        normalize_audio=k.normalize_audio,
        cdg_pixel_scaling=k.cdg_pixel_scaling,
        high_quality=k.high_quality,
        complete_transcode_before_play=k.complete_transcode_before_play,
        avsync=k.avsync,
        limit_user_songs_by=k.limit_user_songs_by,
        buffer_size=k.buffer_size,
        languages=LANGUAGES,
        preferred_language=preferred_language,  # 傳遞當前偏好語言
    )
