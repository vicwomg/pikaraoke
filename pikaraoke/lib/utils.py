import hashlib
import json
from pathlib import Path
from typing import cast
from urllib.parse import quote

import flask
import flask_babel

from pikaraoke import Karaoke
from pikaraoke.lib.get_platform import Platform

translate = flask_babel.gettext
"""Alias for the gettext function from Flask-Babel

This is used for marking strings for translation in the application.

Example usage:
    message = translate("This is a translatable string")
"""


def filename_from_path(file_path: str, remove_youtube_id: bool = True) -> str:
    """Extract the filename from a given file path, optionally removing YouTube ID

    Args:
        file_path (str): The path to the file.
        remove_youtube_id (bool): Removes YouTube ID from the filename by partitioning the name at
            '---' and returning the part before it. Defaults to True.

    Returns:
        str: The extracted filename, optionally without the YouTube ID.
    """
    return (name := Path(file_path).stem).partition("---")[0] if remove_youtube_id else name


def url_escape(filename: str) -> str:
    """Encode a filename to be safely included in a URL

    This function takes a filename, encodes it in UTF-8, and then applies URL encoding
    to make sure all special characters are properly escaped, allowing the filename to
    be safely used as part of a URL. Example: `'abc def' -> 'abc%20def'`

    Args:
        filename (str): The filename to be encoded.

    Returns:
        str: The URL-encoded filename.
    """
    return quote(filename.encode("utf8"))


def hash_dict(dictionary: dict) -> str:
    """Compute an MD5 hash of a dictionary

    This function serializes a dictionary to a JSON string with sorted keys and ensures
    ASCII encoding. It then computes the MD5 hash of the UTF-8 encoded JSON string and
    returns the hexadecimal digest of the hash.

    Args:
        dictionary (dict): The dictionary to be hashed.

    Returns:
        str: The hexadecimal MD5 hash of the JSON-encoded dictionary.
    """
    return hashlib.md5(
        json.dumps(dictionary, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
    ).hexdigest()


def is_admin(password: str | None) -> bool:
    """Determine if the provided password matches the admin cookie value

    This function checks if the provided password is `None` or if it matches
    the value of the "admin" cookie in the current Flask request. If the password
    is `None`, the function assumes the user is an admin. If the "admin" cookie
    is present and its value matches the provided password, the function returns `True`.
    Otherwise, it returns `False`.

    Args:
        password (str): The password to check against the admin cookie value.

    Returns:
        bool: `True` if the password matches the admin cookie or if the password is `None`,
              `False` otherwise.
    """
    return password is None or flask.request.cookies.get("admin") == password


class PiKaraokeServer(flask.Flask):
    """Child class of `Flask` with custom attributes to provide intellisense to the app object"""

    platform: Platform
    karaoke: Karaoke


def get_current_app() -> PiKaraokeServer:
    """Retrieve the current Flask application instance cast to a PiKaraokeServer type

    This function assumes that the Flask application instance is of type PiKaraokeServer,
    which is a custom subclass of Flask. It provides a type-safe way to access the current
    application and its custom attributes. The objective is to get intellisense on the
    current_app object.

    Returns:
        PiKaraokeServer: The current Flask application instance cast to PiKaraokeServer.

    Raises:
        TypeError: If the current application is not of type PiKaraokeServer, a TypeError
        might be raised when performing operations on the casted object.
    """
    return cast(PiKaraokeServer, flask.current_app)
