import logging
import signal
import webbrowser
from contextlib import contextmanager
from urllib.parse import quote

import cherrypy
import flask
from flask import Flask, request
from flask_babel import Babel

from pikaraoke import PiKaraokeServer, filename_from_path
from pikaraoke.config import ConfigType
from pikaraoke.constants import LANGUAGES
from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.get_platform import get_platform
from pikaraoke.lib.logger import configure_logger
from pikaraoke.lib.parse_args import parse_args
from pikaraoke.routes.admin_routes import admin_bp
from pikaraoke.routes.auth_routes import auth_bp
from pikaraoke.routes.file_routes import file_bp
from pikaraoke.routes.home_routes import home_bp
from pikaraoke.routes.karaoke_routes import karaoke_bp

logger = logging.getLogger(__name__)


def create_app(admin_pw: str, karaoke: Karaoke, config_class: ConfigType = ConfigType.DEVELOPMENT):
    # Handle sigterm, apparently cherrypy won't shut down without explicit handling
    signal.signal(signal.SIGTERM, lambda signum, stack_frame: karaoke.stop())

    app: PiKaraokeServer = flask.Flask(__name__)
    app.config.from_object(config_class.value)  # Load config

    # Initialize extensions
    app.jinja_env.add_extension("jinja2.ext.i18n")
    babel = Babel(app)
    babel.init_app(app)

    # Define application-specific attributes
    app.karaoke = karaoke
    app.platform = get_platform()

    if admin_pw:
        app.config["ADMIN_PASSWORD"] = admin_pw

    app.jinja_env.globals.update(filename_from_path=filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    @babel.localeselector
    def get_locale() -> str | None:
        """Select the language to display the webpage in based on the Accept-Language header"""
        return request.accept_languages.best_match(LANGUAGES.keys())

    # Register blueprints or routes here
    app.register_blueprint(home_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(file_bp)
    app.register_blueprint(karaoke_bp)

    return app


@contextmanager
def start_server():
    try:
        logger.debug("Starting server...")
        cherrypy.engine.start()
        yield
    finally:
        logger.debug("Stopping the server...")
        cherrypy.engine.exit()
        logger.debug("Server stopped.")


def configure_server(app: Flask, port: int):
    cherrypy.tree.graft(app, "/")
    cherrypy.config.update(
        {
            "engine.autoreload.on": False,
            "log.screen": True,
            "server.socket_port": port,
            "server.socket_host": "0.0.0.0",
            "server.thread_pool": 100,
        }
    )


def main():
    args = parse_args()
    configure_logger(log_level=logging.DEBUG)

    # setup/create download directory if necessary
    download_path = args.download_path.expanduser()
    download_path.mkdir(parents=True, exist_ok=True)

    karaoke = Karaoke(
        port=args.port,
        ffmpeg_port=args.ffmpeg_port,
        download_path=download_path,
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=args.volume,
        hide_url=args.hide_url,
        hide_raspiwifi_instructions=args.hide_raspiwifi_instructions,
        hide_splash_screen=args.hide_splash_screen,
        high_quality=args.high_quality,
        logo_path=str(args.logo_path),
        hide_overlay=args.hide_overlay,
        screensaver_timeout=args.screensaver_timeout,
        url=args.url,
        ffmpeg_url=args.ffmpeg_url,
        prefer_hostname=args.prefer_hostname,
    )

    app = create_app(
        admin_pw=args.admin_password, karaoke=karaoke, config_class=ConfigType.DEVELOPMENT
    )

    configure_server(app=app, port=args.port)
    configure_logger()  # Because Cherrypy configures its logger differently

    with start_server(), karaoke:
        logger.debug("Server is running.")

        # Start the splash screen using selenium
        if not args.hide_splash_screen:
            url = f"http://{karaoke.ip}:5555/splash"
            logger.debug(f"Opening in default browser at {url}")
            webbrowser.open(url)

        karaoke.run()


if __name__ == "__main__":
    main()
