from pikaraoke.constants import VERSION
from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.get_platform import get_platform

__version__ = VERSION
PACKAGE = __package__

__all__ = [
    "VERSION",
    "PACKAGE",
    Karaoke.__name__,
    get_platform.__name__,
]
