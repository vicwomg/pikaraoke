"""Authorization: a route is host-only unless it is marked public.

The gate is a `before_request`, so it answers ahead of `@bp.arguments`
validation -- an unauthenticated malformed POST gets a 403, not a 422.
"""

from collections.abc import Callable

import flask_babel
from flask import Flask, flash, jsonify, redirect, request, url_for

from pikaraoke.lib.current_app import is_admin

_ = flask_babel.gettext

# Registered by Flask and flask-smorest, so they cannot carry a marker. Never a
# hatch for endpoints we own.
_LIBRARY_ENDPOINTS = frozenset({"static", "api-docs.openapi_json", "api-docs.openapi_swagger_ui"})


def public(view: Callable) -> Callable:
    """This endpoint is open to everyone in the room."""
    view.pika_public = True
    return view


def host_only(message=None, *, json: bool = False) -> Callable:
    """Host-only, refusing with `message` rather than the generic wording.

    `message` takes a `lazy_gettext` string: this runs at import, before a
    request has picked a language. Set `json` on an endpoint that always answers
    JSON but does not sit under `/api`, where the medium would otherwise be read
    off the request. Omitting the decorator does not open a route -- only
    `public` does.
    """

    def wrap(view: Callable) -> Callable:
        view.pika_refusal = message
        view.pika_refusal_json = json
        return view

    return wrap


def install_auth_gate(app: Flask) -> None:
    """Refuse every request whose endpoint is not marked `@public`."""

    @app.before_request
    def require_admin():
        # An unmatched rule is a 404, and Flask's to answer.
        if request.endpoint is None:
            return None
        if request.endpoint in _LIBRARY_ENDPOINTS:
            return None
        view = app.view_functions.get(request.endpoint)
        if getattr(view, "pika_public", False):
            return None
        # Last, because it re-reads config.ini: ~0.4ms on a desktop and several
        # times that on a Pi, which the public routes serving HLS segments skip.
        if is_admin():
            return None
        return _refuse(
            getattr(view, "pika_refusal", None),
            getattr(view, "pika_refusal_json", False),
        )


def _wants_json() -> bool:
    """Whether the caller reads a JSON body rather than a rendered page."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )


def _refuse(message, force_json: bool = False):
    """One refusal for the whole app, in the medium the caller is reading."""
    if force_json or _wants_json():
        return jsonify({"error": "Unauthorized"}), 403
    # flash() puts the message in the session, where only str survives.
    # MSG: Message shown when someone who is not the host tries a host-only action.
    flash(str(message) if message else _("You don't have permission to do that"), "is-danger")
    return redirect(url_for("home.home"))
