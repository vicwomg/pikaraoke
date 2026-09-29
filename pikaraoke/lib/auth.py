"""Authorization: a route is host-only unless it is marked public.

The gate is a `before_request`, so it answers ahead of `@bp.arguments`
validation -- an unauthenticated malformed POST gets a 403, not a 422.
"""

from collections.abc import Callable

import flask_babel
from flask import Flask, flash, jsonify, redirect, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_smorest import Api

from pikaraoke.lib.current_app import get_admin_auth, is_admin

_ = flask_babel.gettext

# Registered by Flask and flask-smorest, so they cannot carry a marker. Never a
# hatch for endpoints we own.
_LIBRARY_ENDPOINTS = frozenset({"static", "api-docs.openapi_json", "api-docs.openapi_swagger_ui"})

_SECURITY_SCHEME = "adminSession"


def public(view: Callable) -> Callable:
    """This endpoint is open to everyone in the room, to the gate and to the spec."""
    view.pika_public = True
    # flask-smorest reads `_apidoc` when `@route` registers the view, so this has
    # to sit below it -- above, the gate opens while the spec still asks for a login.
    apidoc = getattr(view, "_apidoc", {})
    apidoc.setdefault("manual_doc", {})["security"] = []
    view._apidoc = apidoc
    return view


def grant_admin_session() -> None:
    """Make this caller an admin until the password changes or the cookie expires."""
    session["admin"] = get_admin_auth().session_token
    session.permanent = True


def log_in(password: str) -> bool:
    """Establish an admin session if the password is right, and report whether it was.

    Each caller says so in its own medium: the browser flashes, the API answers
    in a status code.
    """
    if not get_admin_auth().verify(password):
        return False
    grant_admin_session()
    return True


def document_auth(app: Flask, api: Api) -> None:
    """Name the credential in the spec, and require it wherever `@public` did not.

    Without this, a client reading `/apidocs` sees the whole API and no way in.
    """
    api.spec.components.security_scheme(
        _SECURITY_SCHEME,
        {
            "type": "apiKey",
            "in": "cookie",
            "name": app.config["SESSION_COOKIE_NAME"],
            "description": "Session cookie issued by POST /api/auth.",
        },
    )
    # Read when the spec is serialised, so this need not run before the blueprints.
    api.spec.options["security"] = [{_SECURITY_SCHEME: []}]


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
        return _refuse()


def _refuse() -> ResponseReturnValue:
    """One refusal for the whole app, in the medium the caller is reading.

    The path is the whole answer: every JSON route sits under `/api` and no page
    does, which `test_route_mediums.py` holds the tree to. Nothing is left to
    guess from the request headers.
    """
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unauthorized"}), 403
    # MSG: Message shown when someone who is not the host tries a host-only action.
    flash(_("You don't have permission to do that"), "is-danger")
    return redirect(url_for("home.home"))
