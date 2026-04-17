"""Flask application entry point and server initialization."""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
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
from pikaraoke.lib.get_platform import get_data_directory, has_js_runtime, is_windows
from pikaraoke.lib.song_manager import SongManager
from pikaraoke.lib.youtube_dl import upgrade_youtubedl
from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.background_music import background_music_bp
from pikaraoke.routes.batch_song_renamer import batch_song_renamer_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.home import home_bp
from pikaraoke.routes.images import images_bp
from pikaraoke.routes.info import info_bp
from pikaraoke.routes.metadata_api import metadata_bp
from pikaraoke.routes.now_playing import nowplaying_bp
from pikaraoke.routes.preferences import preferences_bp
from pikaraoke.routes.queue import queue_bp
from pikaraoke.routes.search import search_bp
from pikaraoke.routes.socket_events import setup_socket_events
from pikaraoke.routes.splash import splash_bp
from pikaraoke.routes.stream import stream_bp

_ = flask_babel.gettext

args = parse_pikaraoke_args()
socketio = SocketIO(async_mode="threading", cors_allowed_origins=args.url)
babel = Babel()


app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
app.config["JSON_SORT_KEYS"] = False

# Always initialize flask-smorest Api for error handling (@bp.arguments validation).
# Only expose the Swagger UI when --enable-swagger is passed.
from flask_smorest import Api

app.config["API_TITLE"] = "PiKaraoke API"
app.config["API_VERSION"] = VERSION
app.config["OPENAPI_VERSION"] = "3.0.2"
app.config["OPENAPI_URL_PREFIX"] = "/"

if args.enable_swagger:
    app.config["OPENAPI_SWAGGER_UI_PATH"] = "/apidocs"
    app.config["OPENAPI_SWAGGER_UI_URL"] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"

api = Api(app)

# Blueprints shown in /apidocs when swagger is enabled
_api_blueprints = [
    queue_bp,
    search_bp,
    files_bp,
    preferences_bp,
    admin_bp,
    controller_bp,
    background_music_bp,
    images_bp,
    nowplaying_bp,
    stream_bp,
    metadata_bp,
]

# Blueprints hidden from /apidocs (internal UI routes)
_internal_blueprints = [
    home_bp,
    info_bp,
    splash_bp,
    batch_song_renamer_bp,
]

for bp in _api_blueprints:
    api.register_blueprint(bp)

for bp in _internal_blueprints:
    app.register_blueprint(bp)


def get_locale() -> str | None:
    """Select the language to display based on user preference or Accept-Language header.

    Returns:
        Language code string (e.g., 'en', 'fr') or None.
    """
    # Check config.ini lang settings (if karaoke instance is initialized)
    try:
        k = get_karaoke_instance()
        preferred_lang = k.preferences.get("preferred_language")
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


def compile_translations() -> None:
    """Compile .po translation files to .mo binary format if needed."""
    translations_dir = Path(__file__).parent / "translations"
    if not translations_dir.exists():
        return

    # Check if any .po file is newer than its .mo counterpart
    needs_compile = False
    for po_file in translations_dir.rglob("*.po"):
        mo_file = po_file.with_suffix(".mo")
        if not mo_file.exists() or po_file.stat().st_mtime > mo_file.stat().st_mtime:
            needs_compile = True
            break

    if not needs_compile:
        return

    print("Compiling translation files...")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "babel.messages.frontend",
            "compile",
            "-f",
            "-d",
            str(translations_dir),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to compile translations: {result.stderr}")
    else:
        print("Translations compiled successfully")


def main() -> None:
    """Main entry point for the PiKaraoke application.

    Initializes the Flask server, Karaoke engine, and splash screen.
    Blocks until the application is terminated.
    """
    compile_translations()

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
        high_quality=args.high_quality,
        logo_path=args.logo_path,
        hide_overlay=args.hide_overlay,
        show_splash_clock=args.show_splash_clock,
        url=args.url,
        prefer_hostname=args.prefer_hostname,
        disable_bg_music=args.disable_bg_music,
        bg_music_volume=args.bg_music_volume,
        bg_music_path=args.bg_music_path,
        disable_bg_video=args.disable_bg_video,
        bg_video_path=args.bg_video_path,
        disable_score=args.disable_score,
        limit_user_songs_by=args.limit_user_songs_by,
        avsync=float(args.avsync) if args.avsync is not None else None,
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

    # Wire download events to SocketIO broadcasts with app context
    from pikaraoke.lib.current_app import broadcast_event

    def _broadcast_in_context(event_name):
        def handler():
            with app.app_context():
                broadcast_event(event_name)

        return handler

    k.events.on("download_started", _broadcast_in_context("download_started"))
    k.events.on("download_stopped", _broadcast_in_context("download_stopped"))

    # expose shared configuration variables to the flask app
    app.config["ADMIN_PASSWORD"] = args.admin_password
    app.config["SITE_NAME"] = "PiKaraoke"

    # Expose some functions to jinja templates
    app.jinja_env.globals.update(filename_from_path=k.song_manager.display_name_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    threading.Thread(target=upgrade_youtubedl, daemon=True).start()

    # Werkzeug's threaded dev server handles WebSockets via simple-websocket.
    # It blocks, so run it on a daemon thread; the karaoke loop stays on the
    # main thread where it can catch KeyboardInterrupt.
    def _serve() -> None:
        socketio.run(
            app,
            host="0.0.0.0",
            port=int(args.port),
            allow_unsafe_werkzeug=True,
            log_output=False,
        )

    threading.Thread(target=_serve, daemon=True).start()

    # Handle sigterm, apparently cherrypy won't shut down without explicit handling
    # signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())

    # Start the splash screen browser only when opted in. By default the user
    # opens the splash URL in their own browser.
    if args.launch_browser:
        browser = Browser(k, args.window_size, args.external_monitor)
        browser.launch_splash_screen()
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
