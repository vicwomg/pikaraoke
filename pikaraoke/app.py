from gevent import monkey

monkey.patch_all()

import hashlib
import json
import logging
import os
import sys

import flask_babel
from flask import Flask, request, session
from flask_babel import Babel
from flask_socketio import SocketIO

from pikaraoke import karaoke
from pikaraoke.constants import LANGUAGES
from pikaraoke.lib.args import parse_pikaraoke_args
from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.ffmpeg import is_ffmpeg_installed
from pikaraoke.lib.file_resolver import delete_tmp_dir
from pikaraoke.lib.get_platform import get_platform, is_raspberry_pi
from pikaraoke.lib.selenium import launch_splash_screen
from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.background_music import background_music_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.home import home_bp
from pikaraoke.routes.images import images_bp
from pikaraoke.routes.info import info_bp
from pikaraoke.routes.preferences import preferences_bp
from pikaraoke.routes.queue import queue_bp
from pikaraoke.routes.search import search_bp
from pikaraoke.routes.splash import splash_bp
from pikaraoke.routes.stream import stream_bp

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

_ = flask_babel.gettext

from gevent.pywsgi import WSGIServer

app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
app.config["JSON_SORT_KEYS"] = False
babel = Babel(app)
raspberry_pi = is_raspberry_pi()


# Register blueprints for additional routes
app.register_blueprint(home_bp)
app.register_blueprint(stream_bp)
app.register_blueprint(preferences_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(background_music_bp)
app.register_blueprint(queue_bp)
app.register_blueprint(images_bp)
app.register_blueprint(files_bp)
app.register_blueprint(search_bp)
app.register_blueprint(info_bp)
app.register_blueprint(splash_bp)
app.register_blueprint(controller_bp)


@babel.localeselector
def get_locale():
    """Select the language to display the webpage in based on the Accept-Language header"""
    if request.args.get("lang"):
        session["lang"] = request.args.get("lang")
        locale = session.get("lang", "en")
    else:
        locale = request.accept_languages.best_match(LANGUAGES.keys())
    return locale


@app.route("/nowplaying")
def nowplaying():
    k = get_karaoke_instance()
    try:
        if len(k.queue) >= 1:
            next_song = k.queue[0]["title"]
            next_user = k.queue[0]["user"]
        else:
            next_song = None
            next_user = None
        rc = {
            "now_playing": k.now_playing,
            "now_playing_user": k.now_playing_user,
            "now_playing_command": k.now_playing_command,
            "now_playing_duration": k.now_playing_duration,
            "now_playing_transpose": k.now_playing_transpose,
            "now_playing_url": k.now_playing_url,
            "up_next": next_song,
            "next_user": next_user,
            "is_paused": k.is_paused,
            "volume": k.volume,
            # "is_transpose_enabled": k.is_transpose_enabled,
        }
        hash = hashlib.md5(
            json.dumps(rc, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
        ).hexdigest()
        rc["hash"] = hash  # used to detect changes in the now playing data
        return json.dumps(rc)
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""


def main():
    platform = get_platform()

    args = parse_pikaraoke_args()

    if not is_ffmpeg_installed():
        logging.error(
            "ffmpeg is not installed, which is required to run PiKaraoke. See: https://www.ffmpeg.org/"
        )
        sys.exit(1)

    # setup/create download directory if necessary
    if not os.path.exists(args.download_path):
        print("Creating download path: " + args.download_path)
        os.makedirs(args.download_path)

    # Configure karaoke process
    k = karaoke.Karaoke(
        port=args.port,
        download_path=args.download_path,
        youtubedl_path=args.youtubedl_path,
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=args.volume,
        normalize_audio=args.normalize_audio,
        complete_transcode_before_play=args.complete_transcode_before_play,
        buffer_size=args.buffer_size,
        hide_url=args.hide_url,
        hide_notifications=args.hide_notifications,
        hide_splash_screen=args.hide_splash_screen,
        high_quality=args.high_quality,
        logo_path=args.logo_path,
        hide_overlay=args.hide_overlay,
        screensaver_timeout=args.screensaver_timeout,
        url=args.url,
        prefer_hostname=args.prefer_hostname,
        disable_bg_music=args.disable_bg_music,
        bg_music_volume=args.bg_music_volume,
        bg_music_path=args.bg_music_path,
        bg_video_path=args.bg_video_path,
        disable_score=args.disable_score,
        limit_user_songs_by=args.limit_user_songs_by,
        config_file_path=args.config_file_path,
    )

    # expose karaoke object to the flask app
    with app.app_context():
        app.k = k

    # expose shared configuration variables to the flask app
    app.config["ADMIN_PASSWORD"] = args.admin_password
    app.config["SITE_NAME"] = "PiKaraoke"

    # Expose some functions to jinja templates
    app.jinja_env.globals.update(filename_from_path=k.filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    k.upgrade_youtubedl()

    server = WSGIServer(("0.0.0.0", int(args.port)), app)
    server.start()

    # Handle sigterm, apparently cherrypy won't shut down without explicit handling
    # signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())

    # force headless mode when on Android
    if (platform == "android") and not args.hide_splash_screen:
        args.hide_splash_screen = True
        logging.info("Forced to run headless mode in Android")

    # Start the splash screen using selenium
    if not args.hide_splash_screen:
        driver = launch_splash_screen(k, args.window_size)
        if not driver:
            sys.exit()
    else:
        driver = None

    # Start the karaoke process
    k.run()

    # Close running processes when done
    if driver is not None:
        driver.close()

    delete_tmp_dir()
    sys.exit()


if __name__ == "__main__":
    main()
