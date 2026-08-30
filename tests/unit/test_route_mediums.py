"""A route's medium is written in its path, not sniffed off the request headers.

The authorization gate refuses in JSON under `/api` and with a redirect
everywhere else. That is the whole rule, and it is only correct while the tree
keeps to it -- so these assert it both ways, off the running `url_map`.
"""

import ast
import inspect
import re
import sys

# render_template_string is deliberately absent: the batch renamer builds a table
# fragment with it and returns it inside a JSON field, which is still JSON.
_ANSWERS_A_BROWSER = re.compile(r"\brender_template\(|\bredirect\(")
_ANSWERS_A_PROGRAM = re.compile(r"\bjsonify\(|\bjson\.dumps\(|\breturn \{")


def _plain_calls(source: str) -> set[str]:
    """Names called bare -- `_do_enqueue(...)`, not `k.queue_manager.enqueue(...)`."""
    return {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _medium_source(view) -> str:
    """The view's source, plus that of the helpers in its own module it calls.

    `enqueue_form` returns JSON only through `_do_enqueue`, so reading the view
    alone leaves any route that delegates its return unguarded.
    """
    module = sys.modules[view.__module__]
    source = inspect.getsource(view)
    for name in _plain_calls(source):
        helper = getattr(module, name, None)
        if inspect.isfunction(helper) and helper.__module__ == view.__module__:
            source += inspect.getsource(helper)
    return source


def _routes(app):
    """Every rule this repo defines, with its view's source unwrapped past
    @bp.arguments, which wraps the real function in flask_smorest's."""
    for rule in app.url_map.iter_rules():
        view = inspect.unwrap(app.view_functions[rule.endpoint])
        if view.__module__.startswith("pikaraoke.routes"):
            yield rule.rule, rule.endpoint, _medium_source(view)


def test_every_json_route_sits_under_api(real_app):
    """Otherwise the gate hands a guest an HTML redirect where the caller reads
    JSON -- on the refusal path, which is the one least likely to be exercised.
    """
    stray = sorted(
        f"{endpoint} at {path}"
        for path, endpoint, source in _routes(real_app)
        if _ANSWERS_A_PROGRAM.search(source) and not path.startswith("/api/")
    )
    assert stray == []


def test_no_page_sits_under_api(real_app):
    """The other direction, which the first alone would let through: a page under
    /api is refused in JSON, which a browser cannot show anyone.
    """
    stray = sorted(
        f"{endpoint} at {path}"
        for path, endpoint, source in _routes(real_app)
        if path.startswith("/api/") and _ANSWERS_A_BROWSER.search(source)
    )
    assert stray == []
