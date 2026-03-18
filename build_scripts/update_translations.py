"""Automated translation workflow for PiKaraoke.

Extracts, updates, auto-translates, and compiles .po translation files.
Uses Google Translate (via deep-translator) for untranslated/fuzzy entries.

Usage:
    python build_scripts/update_translations.py              # Full cycle
    python build_scripts/update_translations.py --extract-po-only  # Extract/update .po files only
    python build_scripts/update_translations.py --translate-only # Only translate + compile
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import polib
from deep_translator import GoogleTranslator

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIKARAOKE_DIR = PROJECT_ROOT / "pikaraoke"
TRANSLATIONS_DIR = PIKARAOKE_DIR / "translations"
POT_FILE = PIKARAOKE_DIR / "messages.pot"
BABEL_CFG = PIKARAOKE_DIR / "babel.cfg"

# Map locale codes (from constants.py) to Google Translate language codes
LOCALE_TO_GOOGLE = {
    "de_DE": "de",
    "es_VE": "es",
    "fi_FI": "fi",
    "fr_FR": "fr",
    "id_ID": "id",
    "it_IT": "it",
    "ja_JP": "ja",
    "ko_KR": "ko",
    "nl_NL": "nl",
    "no_NO": "no",
    "pt_BR": "pt",
    "ru_RU": "ru",
    "th_TH": "th",
    "zh_Hans_CN": "zh-CN",
    "zh_Hant_TW": "zh-TW",
}

AUTO_TRANSLATED_COMMENT = "auto-translated"

# Rate limiting: seconds between Google Translate requests
REQUEST_DELAY = 0.5


def run_pybabel(args: list[str]) -> None:
    """Run a pybabel command, raising on failure."""
    cmd = [sys.executable, "-m", "babel.messages.frontend"] + args
    print(f"  Running: pybabel {' '.join(args)}")
    result = subprocess.run(cmd, cwd=str(PIKARAOKE_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  STDERR: {result.stderr}")
        raise RuntimeError(f"pybabel {args[0]} failed (exit {result.returncode})")
    if result.stdout:
        print(f"  {result.stdout.strip()}")


def extract() -> None:
    """Extract translatable strings from source into messages.pot."""
    print("\n--- Extract ---")
    run_pybabel(
        [
            "extract",
            "-F",
            str(BABEL_CFG),
            "-o",
            str(POT_FILE),
            "--add-comments=MSG:",
            "--strip-comment-tags",
            "--sort-by-file",
            ".",
        ]
    )
    print(f"  Updated {POT_FILE.name}")


def update() -> None:
    """Sync .pot changes into all .po files."""
    print("\n--- Update ---")
    run_pybabel(
        [
            "update",
            "-i",
            str(POT_FILE),
            "-d",
            str(TRANSLATIONS_DIR),
        ]
    )


def translate_entry(entry: polib.POEntry, translator: GoogleTranslator) -> str | None:
    """Translate a single PO entry. Returns translated text or None on failure."""
    source = entry.msgid
    if not source.strip():
        return None
    try:
        translated = translator.translate(source)
        time.sleep(REQUEST_DELAY)
        return translated
    except Exception as e:
        print(f"    Failed to translate '{source[:50]}...': {e}")
        return None


def auto_translate() -> None:
    """Find untranslated and fuzzy entries in all .po files and translate them."""
    print("\n--- Auto-translate ---")

    for locale, google_code in LOCALE_TO_GOOGLE.items():
        po_path = TRANSLATIONS_DIR / locale / "LC_MESSAGES" / "messages.po"
        if not po_path.exists():
            print(f"  Skipping {locale}: {po_path} not found")
            continue

        po = polib.pofile(str(po_path))
        untranslated = po.untranslated_entries()
        fuzzy = po.fuzzy_entries()
        entries_to_translate = untranslated + fuzzy

        if not entries_to_translate:
            print(f"  {locale}: nothing to translate")
            continue

        print(
            f"  {locale}: {len(entries_to_translate)} entries "
            f"({len(untranslated)} untranslated, {len(fuzzy)} fuzzy)"
        )

        translator = GoogleTranslator(source="en", target=google_code)
        translated_count = 0

        for entry in entries_to_translate:
            result = translate_entry(entry, translator)
            if result is None:
                continue

            entry.msgstr = result
            # Remove fuzzy flag if present
            if "fuzzy" in entry.flags:
                entry.flags.remove("fuzzy")
            # Add auto-translated comment for human review
            if AUTO_TRANSLATED_COMMENT not in (entry.comment or ""):
                existing = entry.comment or ""
                entry.comment = (
                    f"{existing}\n{AUTO_TRANSLATED_COMMENT}"
                    if existing
                    else AUTO_TRANSLATED_COMMENT
                )
            translated_count += 1

        po.save(str(po_path))
        print(f"  {locale}: translated {translated_count}/{len(entries_to_translate)} entries")


def compile_translations() -> None:
    """Compile .po files to .mo binary format."""
    print("\n--- Compile ---")
    run_pybabel(
        [
            "compile",
            "-f",
            "-d",
            str(TRANSLATIONS_DIR),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated translation workflow for PiKaraoke")
    parser.add_argument(
        "--extract-po-only",
        action="store_true",
        help="Extract and update .po files only, skip auto-translation",
    )
    parser.add_argument(
        "--translate-only",
        action="store_true",
        help="Skip extract/update, only translate and compile",
    )
    args = parser.parse_args()

    if args.extract_po_only and args.translate_only:
        parser.error("--extract-po-only and --translate-only are mutually exclusive")

    if not args.translate_only:
        extract()
        update()

    if not args.extract_po_only:
        auto_translate()

    compile_translations()
    print("\nDone!")


if __name__ == "__main__":
    main()
