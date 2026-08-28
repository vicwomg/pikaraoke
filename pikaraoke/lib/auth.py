"""Authorization: a route is host-only unless it is marked public.

The gate is a `before_request`, so it answers ahead of `@bp.arguments`
validation -- an unauthenticated malformed POST gets a 403, not a 422.
"""

from collections.abc import Callable

import flask_babel
from flask import Flask, flash, jsonify, redirect, request, url_for
from flask.typing import ResponseReturnValue

from pikaraoke.lib.current_app import is_admin

_ = flask_babel.gettext

# Registered by Flask and flask-smorest, so they cannot carry a marker. Never a
# hatch for endpoints we own.
_LIBRARY_ENDPOINTS = frozenset({"static", "api-docs.openapi_json", "api-docs.openapi_swagger_ui"})


def public(view: Callable) -> Callable:
    """This endpoint is open to everyone in the room."""
    view.pika_public = True
    return view


def answers_json(view: Callable) -> Callable:
    """Refuse in JSON: this endpoint returns JSON but does not sit under `/api`.

    Without it the medium is read off the request, which for these routes means
    trusting `X-Requested-With` -- correct today only because every caller
    happens to be jQuery. Marking a route does not gate it; only omitting
    `public` does.
    """
    view.pika_refusal_json = True
    return view


def install_auth_gate(app: Flask) -> None:
    """Refuse every request whose endpoint is not marked `@public`."""

    @app.before_request
    def require_admin() -> ResponseReturnValue | None:
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
        return _refuse(getattr(view, "pika_refusal_json", False))


def _refuse(force_json: bool) -> ResponseReturnValue:
    """One refusal for the whole app, in the medium the caller is reading."""
    wants_json = (
        force_json
        or request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return jsonify({"error": "Unauthorized"}), 403
    # MSG: Message shown when someone who is not the host tries a host-only action.
    flash(_("You don't have permission to do that"), "is-danger")
    return redirect(url_for("home.home"))
