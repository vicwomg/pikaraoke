"""Blueprint registration, in one place so the app and its tests cannot disagree.

A blueprint is all-or-nothing in `/apidocs`: flask-smorest documents everything
registered on the `Api` and nothing registered on the `Flask` app. Membership
here is therefore the whole of what the API list means.
"""

from pikaraoke.routes.admin import admin_bp
from pikaraoke.routes.auth_api import auth_api_bp
from pikaraoke.routes.background_music import background_music_bp
from pikaraoke.routes.batch_song_renamer import batch_song_renamer_bp
from pikaraoke.routes.controller import controller_bp
from pikaraoke.routes.files import files_bp
from pikaraoke.routes.home import home_bp
from pikaraoke.routes.images import images_bp
from pikaraoke.routes.info import info_bp
from pikaraoke.routes.library_api import library_bp
from pikaraoke.routes.metadata_api import metadata_bp
from pikaraoke.routes.now_playing import nowplaying_bp
from pikaraoke.routes.preferences import preferences_bp
from pikaraoke.routes.queue import queue_bp
from pikaraoke.routes.search import search_bp
from pikaraoke.routes.sessions import sessions_bp
from pikaraoke.routes.sessions_api import sessions_api_bp
from pikaraoke.routes.splash import splash_bp
from pikaraoke.routes.stream import stream_bp

# Shown in /apidocs when swagger is enabled. Membership is audience, not medium:
# `/api` says which refusal the gate sends, this says what a stranger should
# script against.
API_BLUEPRINTS = [
    auth_api_bp,
    queue_bp,
    search_bp,
    preferences_bp,
    controller_bp,
    library_bp,
    nowplaying_bp,
    metadata_bp,
    sessions_api_bp,
]

# Hidden from /apidocs: pages, media byte streams, and the JSON only this app's
# own screens poll. Four carry an /api/ route, kept out with the screen they
# serve because the split is per blueprint, not per route.
INTERNAL_BLUEPRINTS = [
    home_bp,
    admin_bp,
    sessions_bp,
    files_bp,
    images_bp,
    stream_bp,
    background_music_bp,
    info_bp,
    splash_bp,
    batch_song_renamer_bp,
]
