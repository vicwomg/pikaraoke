"""Info page: the QR code to join, and system information."""

import flask_babel
import psutil
from flask import jsonify, render_template
from flask_smorest import Blueprint

from pikaraoke import VERSION
from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.get_platform import get_installed_js_runtime

_ = flask_babel.gettext


info_bp = Blueprint("info", __name__)


@info_bp.route("/info")
@public
def info():
    """The QR code to join, and system information."""
    k = get_karaoke_instance()
    return render_template(
        "info.html",
        site_title=get_site_name(),
        # MSG: Title of the page with the QR code to join and the system information.
        title=_("Info"),
        url=k.url,
        admin=is_admin(),
        platform=k.platform,
        os_version=k.os_version,
        ffmpeg_version=k.ffmpeg_version,
        is_transpose_enabled=k.is_transpose_enabled,
        youtubedl_version=k.youtubedl_version,
        js_runtime=get_installed_js_runtime(),
        pikaraoke_version=VERSION,
    )


@info_bp.route("/api/info/stats")
def get_system_stats():
    """Get system statistics (CPU, Memory, Disk).

    Returns:
        JSON response with system stats.
    """
    # cpu
    try:
        # We can afford to block a bit here since it is async
        cpu = str(psutil.cpu_percent(interval=1)) + "%"
    except:
        cpu = _("CPU usage query unsupported")

    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available / 1024.0 / 1024.0, 1)
    total = round(memory.total / 1024.0 / 1024.0, 1)
    memory_str = (
        str(available) + "MB free / " + str(total) + "MB total ( " + str(memory.percent) + "% )"
    )

    # disk
    disk = psutil.disk_usage("/")
    free = round(disk.free / 1024.0 / 1024.0 / 1024.0, 1)
    total = round(disk.total / 1024.0 / 1024.0 / 1024.0, 1)
    disk_str = str(free) + "GB free / " + str(total) + "GB total ( " + str(disk.percent) + "% )"

    return jsonify({"cpu": cpu, "memory": memory_str, "disk": disk_str})
