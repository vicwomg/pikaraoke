from gevent import monkey

monkey.patch_all()

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
from pikaraoke.lib.get_platform import get_platform
from pikaraoke.lib.selenium import launch_splash_screen
from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.background_music import background_music_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.home import home_bp
from pikaraoke.routes.images import images_bp
from pikaraoke.routes.info import info_bp
from pikaraoke.routes.now_playing import nowplaying_bp
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

import threading
import time

from gevent.pywsgi import WSGIServer

args = parse_pikaraoke_args()
socketio = SocketIO(async_mode="gevent", cors_allowed_origins=args.url)
babel = Babel()


app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
app.config["JSON_SORT_KEYS"] = False

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
app.register_blueprint(nowplaying_bp)

babel.init_app(app)
socketio.init_app(app)


@babel.localeselector
def get_locale():
    """Select the language to display the webpage in based on the Accept-Language header"""
    # Check config.ini lang settings
    k = get_karaoke_instance()
    preferred_lang = k.get_user_preference("preferred_language")
    if preferred_lang and preferred_lang in LANGUAGES.keys():
        return preferred_lang
    # Check URL arguments
    elif request.args.get("lang"):
        session["lang"] = request.args.get("lang")
        locale = session.get("lang", "en")
    # Use browser header
    else:
        locale = request.accept_languages.best_match(LANGUAGES.keys())
    return locale


# Handle all the socketio incoming events here.
# TODO: figure out how to move to a blueprint file if this gets out of hand


@socketio.on("end_song")
def end_song(reason):
    k = get_karaoke_instance()
    k.end_song(reason)


@socketio.on("start_song")
def start_song():
    k = get_karaoke_instance()
    k.start_song()


@socketio.on("clear_notification")
def clear_notification():
    k = get_karaoke_instance()
    k.reset_now_playing_notification()


def poll_karaoke_state(k: karaoke.Karaoke):
    curr_now_playing_hash = None
    curr_queue_hash = None
    curr_notification = None
    poll_interval = 0.5
    while True:
        time.sleep(poll_interval)
        np_hash = k.now_playing_hash
        if np_hash != curr_now_playing_hash:
            curr_now_playing_hash = np_hash
            logging.debug(k.get_now_playing())
            socketio.emit("now_playing", k.get_now_playing(), namespace="/")
        q_hash = k.queue_hash
        if q_hash != curr_queue_hash:
            curr_queue_hash = q_hash
            logging.debug(k.queue)
            socketio.emit("queue_update", namespace="/")
        notification = k.now_playing_notification
        if notification != curr_notification:
            curr_notification = notification
            if notification:
                socketio.emit("notification", notification, namespace="/")


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
        youtubedl_proxy=args.youtubedl_proxy,
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
        avsync=args.avsync,
        config_file_path=args.config_file_path,
        cdg_pixel_scaling=args.cdg_pixel_scaling,
        additional_ytdl_args=getattr(args, "ytdl_args", None),
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

    server = WSGIServer(("0.0.0.0", int(args.port)), app, log=None, error_log=logging.getLogger())
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

    # Poll karaoke object for now playing updates
    thread = threading.Thread(target=poll_karaoke_state, args=(k,))
    thread.daemon = True
    thread.start()

    # Start the karaoke process
    k.run()

    # Close running processes when done
    if driver is not None:
        driver.close()

    delete_tmp_dir()
    sys.exit()


if __name__ == "__main__":
    main()
