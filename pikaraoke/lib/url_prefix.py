"""Helpers for serving PiKaraoke from a URL path prefix."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def normalize_url_base_path(path: str | None) -> str:
    """Normalize a configured URL base path for consistent routing."""
    if path is None:
        return ""

    normalized = path.strip()
    if not normalized or normalized == "/":
        return ""

    normalized = normalized.strip("/")
    if not normalized:
        return ""

    return f"/{normalized}"


def append_base_path_to_url(url: str, base_path: str) -> str:
    """Append a configured base path to a public URL when appropriate."""
    if not base_path:
        return url

    split_url = urlsplit(url)
    existing_path = split_url.path.rstrip("/")
    if existing_path and existing_path != base_path:
        return url

    return urlunsplit(
        (
            split_url.scheme,
            split_url.netloc,
            base_path,
            split_url.query,
            split_url.fragment,
        )
    )


class BasePathMiddleware:
    """Expose the app under a configured URL prefix without duplicating routes."""

    def __init__(self, app: Callable[..., Any], base_path: str):
        self.app = app
        self.base_path = normalize_url_base_path(base_path)

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        if not self.base_path:
            return self.app(environ, start_response)

        path_info = environ.get("PATH_INFO", "") or ""
        script_name = environ.get("SCRIPT_NAME", "") or ""

        if path_info == self.base_path:
            path_info = "/"
        elif path_info.startswith(f"{self.base_path}/"):
            path_info = path_info[len(self.base_path) :]

        environ["PATH_INFO"] = path_info or "/"
        environ["SCRIPT_NAME"] = f"{script_name}{self.base_path}"
        return self.app(environ, start_response)
