"""The admin Singers page: the roster, and merge and delete."""

import flask_babel
from flask import render_template
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_site_name

_ = flask_babel.gettext

singers_bp = Blueprint("singers", __name__)

# The whole roster loads in one request and is sorted and paged in the browser,
# so this is a client-side slice size, not an API limit. Singers total in the
# tens to low hundreds -- a name per line, so a larger page than the sessions
# list, which gains an expanding row per night.
SINGERS_PAGE_SIZE = 50


@singers_bp.route("/singers")
def singers():
    """The singer roster, where an operator merges and deletes identities.

    Admin-only by omission, as the Sessions page is.
    """
    return render_template(
        "singers.html",
        site_title=get_site_name(),
        # MSG: Title of the admin page listing every singer, where they are merged and deleted.
        title=_("Singers"),
        page_size=SINGERS_PAGE_SIZE,
    )
