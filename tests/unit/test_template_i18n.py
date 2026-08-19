"""Guards against translated text breaking inline JS in templates.

Jinja's i18n extension returns gettext output as Markup, so translations are never
autoescaped. A translation containing a quote (French "La file d'attente est vide")
terminates the JS string literal it is interpolated into, raising a SyntaxError that
kills the entire inline <script> for that locale. Every gettext call inside a <script>
must therefore be escaped by hand: |tojson to emit a JS value, or |forceescape when JS
builds the translation into an HTML string.

It also guards the placeholder convention. A token that a call site fills in with
replace() must be braced, like {name}: a bare English word is translated along with the
sentence around it, so the replace() matches nothing and the token reaches the screen.
"""

import json
import re
from pathlib import Path

import pytest
from flask import Flask, render_template
from flask_babel import Babel, force_locale

from pikaraoke.constants import LANGUAGES

PIKARAOKE = Path(__file__).resolve().parents[2] / "pikaraoke"
TEMPLATES = PIKARAOKE / "templates"

TRANSLATIONS_OBJECT = re.compile(r"window\.translations\s*=\s*\{(.*?)\n\s*\};", re.DOTALL)
TRANSLATION_VALUE = re.compile(r"^\s*\w+:\s*(.+?),?$", re.MULTILINE)

SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
JINJA_EXPRESSION = re.compile(r"\{\{.*?\}\}", re.DOTALL)
GETTEXT_CALL = re.compile(r"\b(_|gettext|ngettext)\s*\(")
ESCAPING_FILTER = re.compile(r"\|\s*(tojson|forceescape)\b")

GETTEXT_STRING = re.compile(r"\b(?:_|gettext|ngettext)\s*\(\s*(['\"])(.*?)\1")
REPLACE_TARGET = re.compile(r"\breplace\(\s*(['\"])(.*?)\1")
BARE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

# Minified bundles substitute uppercase strings of their own, which would read as offenders.
OWN_SOURCES = sorted(TEMPLATES.glob("**/*.html")) + sorted(
    path for path in (PIKARAOKE / "static").glob("*.js") if not path.name.endswith(".min.js")
)


@pytest.fixture(scope="module")
def app() -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.jinja_env.add_extension("jinja2.ext.i18n")
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(PIKARAOKE / "translations")
    Babel(app)
    return app


@pytest.mark.parametrize("locale", sorted(LANGUAGES))
def test_window_translations_are_valid_js_string_literals(app, locale):
    """Render base.html per locale (blank_page skips the nav's url_for endpoints)."""
    with app.test_request_context(), force_locale(locale):
        html = render_template("base.html", blank_page=True, site_title="PiKaraoke")

    body = TRANSLATIONS_OBJECT.search(html)
    assert body, "window.translations object not found in base.html"

    values = TRANSLATION_VALUE.findall(body.group(1))
    assert values
    for value in values:
        # Raises JSONDecodeError if the translation broke out of its string literal
        assert json.loads(value)


@pytest.mark.parametrize("template", sorted(TEMPLATES.glob("**/*.html")), ids=lambda p: p.name)
def test_gettext_in_script_blocks_is_escaped(template):
    """Enforce the |tojson / |forceescape convention on every gettext call in inline JS."""
    source = template.read_text(encoding="utf-8")
    offenders = [
        f"{template.name}:{source.count(chr(10), 0, block.start(1) + call.start()) + 1}: {call.group()}"
        for block in SCRIPT_BLOCK.finditer(source)
        for call in JINJA_EXPRESSION.finditer(block.group(1))
        if GETTEXT_CALL.search(call.group()) and not ESCAPING_FILTER.search(call.group())
    ]

    assert not offenders, "Unescaped gettext in inline JS:\n" + "\n".join(offenders)


def test_substituted_tokens_in_translated_strings_are_braced():
    """Flag an uppercase word that is both inside a msgid and a target of replace().

    Cross-file by necessity: base.html holds the msgid whose token spa-navigation.js
    substitutes. Words that are merely uppercase (CDG, ENTIRE) are never replace() targets,
    so no vocabulary list is needed to tell a placeholder from emphasis.
    """
    sources = {path: path.read_text(encoding="utf-8") for path in OWN_SOURCES}
    substituted = {
        match.group(2) for text in sources.values() for match in REPLACE_TARGET.finditer(text)
    }

    offenders = [
        f"{path.name}: {token} in {call.group(2)[:60]!r}"
        for path, text in sources.items()
        for call in GETTEXT_STRING.finditer(text)
        for token in BARE_TOKEN.findall(call.group(2))
        if token in substituted
    ]

    assert not offenders, "Substituted tokens must be braced, like {name}:\n" + "\n".join(offenders)
