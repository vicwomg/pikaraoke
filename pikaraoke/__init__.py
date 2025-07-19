from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.get_platform import get_platform
from pikaraoke.version import __version__

PACKAGE = __package__
VERSION = __version__

__all__ = [
    "VERSION",
    "PACKAGE",
    Karaoke.__name__,
    get_platform.__name__,
]
