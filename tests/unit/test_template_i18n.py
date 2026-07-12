"""Guards against translated text breaking inline JS in templates.

Jinja's i18n extension returns gettext output as Markup, so translations are never
autoescaped. A translation containing a quote (Italian "l'intervallo", French "C'est")
used to terminate the JS string literal it was interpolated into, raising a SyntaxError
that killed the entire inline <script> block for that locale.
"""

import json
import re
from pathlib import Path

import pytest
from flask import Flask, render_template
from flask_babel import Babel

PIKARAOKE = Path(__file__).resolve().parents[2] / "pikaraoke"
TEMPLATES = PIKARAOKE / "templates"
TRANSLATIONS = PIKARAOKE / "translations"

LOCALES = ["en"] + sorted(p.name for p in TRANSLATIONS.iterdir() if p.is_dir())

TRANSLATIONS_OBJECT = re.compile(r"window\.translations\s*=\s*\{(.*?)\n\s*\};", re.DOTALL)

# An unfiltered gettext call wrapped in quotes inside a <script> block: the pattern this
# bug class lives in. Use |tojson (JS string literal) or |forceescape (HTML built in JS).
UNFILTERED_GETTEXT = re.compile(r"""['"]\s*\{\{\s*_\([^|}]*\)\s*\}\}""")
SCRIPT_TAG = re.compile(r"</?script", re.IGNORECASE)


def _render_base(locale: str) -> str:
    """Render base.html standalone (blank_page skips the nav's url_for endpoints)."""
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.jinja_env.add_extension("jinja2.ext.i18n")
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(TRANSLATIONS)
    Babel(app, locale_selector=lambda: locale)
    with app.test_request_context():
        return render_template("base.html", blank_page=True, site_title="PiKaraoke")


@pytest.mark.parametrize("locale", LOCALES)
def test_window_translations_are_valid_js_string_literals(locale):
    html = _render_base(locale)
    body = TRANSLATIONS_OBJECT.search(html)
    assert body, "window.translations object not found in base.html"

    entries = {}
    for line in body.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        key, _, value = line.partition(":")
        # Raises JSONDecodeError if the translation broke out of its string literal
        entries[key.strip()] = json.loads(value.strip().rstrip(","))

    assert entries
    assert all(isinstance(value, str) and value for value in entries.values())


@pytest.mark.parametrize("template", sorted(TEMPLATES.glob("*.html")), ids=lambda p: p.name)
def test_no_quote_wrapped_gettext_in_script_blocks(template):
    """Enforce the |tojson / |forceescape convention on every template."""
    in_script = False
    offenders = []
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), start=1):
        for tag in SCRIPT_TAG.findall(line):
            in_script = not tag.startswith("</")
        if in_script and UNFILTERED_GETTEXT.search(line):
            offenders.append(f"{template.name}:{number}: {line.strip()}")

    assert not offenders, "Quote-wrapped gettext in inline JS:\n" + "\n".join(offenders)
