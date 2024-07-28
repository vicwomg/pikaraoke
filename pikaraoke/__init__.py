from pikaraoke.constants import VERSION
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

__version__ = VERSION
PACKAGE = __package__

__all__ = [
    "VERSION",
    "PACKAGE",
    Karaoke.__name__,
    filename_from_path.__name__,
    url_escape.__name__,
    hash_dict.__name__,
    is_admin.__name__,
    get_current_app.__name__,
    PiKaraokeServer.__name__,
    translate.__name__,
    Platform.__name__,
    get_platform.__name__,
]
