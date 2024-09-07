from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.get_platform import Platform, get_platform
from pikaraoke.lib.utils import (
    PiKaraokeServer,
    filename_from_path,
    get_current_app,
    hash_dict,
    is_admin,
    translate,
    url_escape,
)
from pikaraoke.version import __version__

PACKAGE = __package__
VERSION = __version__

__all__ = [
    filename_from_path.__name__,
    get_current_app.__name__,
    get_platform.__name__,
    hash_dict.__name__,
    is_admin.__name__,
    Karaoke.__name__,
    "PACKAGE",
    PiKaraokeServer.__name__,
    Platform.__name__,
    translate.__name__,
    url_escape.__name__,
    "VERSION",
]
