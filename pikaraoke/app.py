"""Flask application entry point and server initialization."""

from gevent import monkey, spawn

monkey.patch_all()

import logging
import os
import sys
from urllib.parse import quote

import flask_babel
from flask import Flask, request, session
from flask_babel import Babel
from flask_socketio import SocketIO

from pikaraoke import VERSION, karaoke
from pikaraoke.constants import LANGUAGES
from pikaraoke.lib.args import parse_pikaraoke_args
from pikaraoke.lib.browser import Browser
from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.ffmpeg import is_ffmpeg_installed
from pikaraoke.lib.file_resolver import delete_tmp_dir
from pikaraoke.lib.get_platform import (
    get_data_directory,
    get_platform,
    has_js_runtime,
    is_windows,
)
from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.background_music import background_music_bp
from pikaraoke.routes.batch_song_renamer import batch_song_renamer_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.home import home_bp
from pikaraoke.routes.images import images_bp
from pikaraoke.routes.info import info_bp
from pikaraoke.routes.now_playing import nowplaying_bp
from pikaraoke.routes.preferences import preferences_bp
from pikaraoke.routes.queue import queue_bp
from pikaraoke.routes.search import search_bp
from pikaraoke.routes.socket_events import setup_socket_events
from pikaraoke.routes.splash import splash_bp
from pikaraoke.routes.stream import stream_bp

_ = flask_babel.gettext

from gevent.pywsgi import WSGIServer

args = parse_pikaraoke_args()
socketio = SocketIO(async_mode="gevent", cors_allowed_origins=args.url)
babel = Babel()


app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
app.config["JSON_SORT_KEYS"] = False
# Initialize Swagger API docs if enabled via CLI flag
app.config["SWAGGER"] = {
    "title": "PiKaraoke API",
    "description": "API for controlling PiKaraoke - a KTV-style karaoke system",
    "version": VERSION,
    "termsOfService": "",
    "hide_top_bar": True,
}
if args.enable_swagger:
    try:
        from flasgger import Swagger

        Swagger(app)
    except ImportError:
        logging.warning("flasgger not installed. Swagger API docs disabled.")

# Register blueprints for additional routes
app.register_blueprint(home_bp)
app.register_blueprint(stream_bp)
app.register_blueprint(preferences_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(background_music_bp)
app.register_blueprint(batch_song_renamer_bp)
app.register_blueprint(queue_bp)
app.register_blueprint(images_bp)
app.register_blueprint(files_bp)
app.register_blueprint(search_bp)
app.register_blueprint(info_bp)
app.register_blueprint(splash_bp)
app.register_blueprint(controller_bp)
app.register_blueprint(nowplaying_bp)


def get_locale() -> str | None:
    """Select the language to display based on user preference or Accept-Language header.

    Returns:
        Language code string (e.g., 'en', 'fr') or None.
    """
    # Check config.ini lang settings (if karaoke instance is initialized)
    try:
        k = get_karaoke_instance()
        preferred_lang = k.get_user_preference("preferred_language")
        if preferred_lang and preferred_lang in LANGUAGES.keys():
            return preferred_lang
    except (RuntimeError, AttributeError):
        # App context not available or karaoke instance not initialized yet
        pass

    # Check URL arguments
    if request.args.get("lang"):
        session["lang"] = request.args.get("lang")
        locale = session.get("lang", "en")
    # Use browser header
    else:
        locale = request.accept_languages.best_match(LANGUAGES.keys())
    return locale


babel.init_app(app, locale_selector=get_locale)
socketio.init_app(app)
setup_socket_events(socketio)


def main() -> None:
    """Main entry point for the PiKaraoke application.

    Initializes the Flask server, Karaoke engine, and splash screen.
    Blocks until the application is terminated.
    """
    platform = get_platform()

    args = parse_pikaraoke_args()

    # --- LOGGING SETUP ---
    # Optional: Force the log file to go to AppData too, so you can debug installation issues
    # log_path = os.path.join(get_data_directory(), 'pikaraoke.log')
    # logging.basicConfig(filename=log_path, level=logging.INFO)

    if not is_ffmpeg_installed():
        logging.error(
            "ffmpeg is not installed, which is required to run PiKaraoke. See: https://www.ffmpeg.org/"
        )
        sys.exit(1)

    if not has_js_runtime():
        logging.warning(
            "No js runtime is installed (such as Deno, Bun, Node.js, or QuickJS). This is required to run yt-dlp. Some downloads may not work. See: https://github.com/yt-dlp/yt-dlp/wiki/EJS"
        )

    # setup/create download directory if necessary
    if not os.path.exists(args.download_path):
        print("Creating download path: " + args.download_path)
        os.makedirs(args.download_path)

    # Configure karaoke process
    k = karaoke.Karaoke(
        port=args.port,
        download_path=args.download_path,
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
        disable_bg_video=args.disable_bg_video,
        bg_video_path=args.bg_video_path,
        disable_score=args.disable_score,
        limit_user_songs_by=args.limit_user_songs_by,
        avsync=float(args.avsync),
        config_file_path=args.config_file_path,
        cdg_pixel_scaling=args.cdg_pixel_scaling,
        streaming_format=args.streaming_format,
        additional_ytdl_args=getattr(args, "ytdl_args", None),
        socketio=socketio,
        preferred_language=args.preferred_language,
    )

    # expose karaoke object to the flask app
    with app.app_context():
        app.config["KARAOKE_INSTANCE"] = k
        # Pass app instance to download manager for background thread context
        k.download_manager.app = app

    # expose shared configuration variables to the flask app
    app.config["ADMIN_PASSWORD"] = args.admin_password
    app.config["SITE_NAME"] = "PiKaraoke"

    # Expose some functions to jinja templates
    app.jinja_env.globals.update(filename_from_path=k.filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    spawn(k.upgrade_youtubedl)

    server = WSGIServer(("0.0.0.0", int(args.port)), app, log=None, error_log=logging.getLogger())
    server.start()

    # Handle sigterm, apparently cherrypy won't shut down without explicit handling
    # signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())

    # force headless mode when on Android
    if (platform == "android") and not args.hide_splash_screen:
        args.hide_splash_screen = True
        logging.info("Forced to run headless mode in Android")

    # Start the splash screen browser
    if not args.hide_splash_screen:
        browser = Browser(k, args.window_size, args.external_monitor)
        browser.launch_splash_screen()
        if not browser:
            logging.error("Failed to launch splash screen browser")
            sys.exit()
    else:
        browser = None

    if args.enable_swagger:
        logging.info(f"Swagger API docs enabled at {k.url}/apidocs")

    # Start the karaoke process
    k.run()

    # Close running browser when done
    if browser is not None:
        browser.close()

    delete_tmp_dir()
    sys.exit()


if __name__ == "__main__":
    main()
