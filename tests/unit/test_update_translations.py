"""Unit tests for placeholder protection and retry in update_translations."""

import pytest

pytest.importorskip("polib", reason="polib is only in the 'translations' dependency group")

import polib
from deep_translator.exceptions import (
    LanguageNotSupportedException,
    TranslationNotFound,
)

from build_scripts.update_translations import (
    _protect_placeholders,
    _restore_placeholders,
    _translate_once,
    _validate_placeholders,
    translate_entry,
)


class FakeTranslator:
    """Fails its first `failures` calls with `error`, then succeeds."""

    def __init__(self, failures: int = 0, error: type[Exception] = TranslationNotFound):
        self.calls = 0
        self.failures = failures
        self.error = error

    def translate(self, text: str) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error("translator unavailable")
        return f"translated {text}"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Skip the rate-limit and retry delays so the suite stays fast."""
    monkeypatch.setattr("build_scripts.update_translations.time.sleep", lambda _: None)


class TestProtectPlaceholders:
    def test_simple_percent_s(self):
        text = "Downloaded: %s"
        protected, tokens = _protect_placeholders(text)
        assert tokens == ["%s"]
        assert "%s" not in protected
        assert "<x0>" in protected

    def test_multiple_percent_s(self):
        text = "Transposing by %s semitones: %s"
        protected, tokens = _protect_placeholders(text)
        assert tokens == ["%s", "%s"]
        assert "<x0>" in protected
        assert "<x1>" in protected

    def test_percent_d(self):
        text = "Download queued (#%d): %s"
        protected, tokens = _protect_placeholders(text)
        assert tokens == ["%d", "%s"]

    def test_named_placeholder(self):
        text = "URL of %(site_title)s:"
        protected, tokens = _protect_placeholders(text)
        assert tokens == ["%(site_title)s"]
        assert "<x0>" in protected

    def test_html_tags(self):
        text = '<a onClick="handleConfirmation()">confirm</a>'
        protected, tokens = _protect_placeholders(text)
        assert len(tokens) == 2
        assert '<a onClick="handleConfirmation()">' in tokens
        assert "</a>" in tokens

    def test_mixed_placeholders_and_html(self):
        text = "<small><i>'%(search_term)s'</i></small>"
        protected, tokens = _protect_placeholders(text)
        assert "%(search_term)s" in tokens
        assert "<small>" in tokens
        assert "</small>" in tokens

    def test_no_placeholders(self):
        text = "Hello world"
        protected, tokens = _protect_placeholders(text)
        assert protected == "Hello world"
        assert tokens == []

    def test_literal_percent_percent(self):
        text = "100%% complete"
        protected, tokens = _protect_placeholders(text)
        assert tokens == ["%%"]


class TestRestorePlaceholders:
    def test_roundtrip(self):
        text = "Downloaded and queued: %s"
        protected, tokens = _protect_placeholders(text)
        # Simulate translation of the non-placeholder part
        translated_protected = protected.replace(
            "Downloaded and queued:", "Heruntergeladen und eingereiht:"
        )
        restored = _restore_placeholders(translated_protected, tokens)
        assert restored == "Heruntergeladen und eingereiht: %s"

    def test_roundtrip_named(self):
        text = "URL of %(site_title)s:"
        protected, tokens = _protect_placeholders(text)
        translated_protected = protected.replace("URL of", "URL von")
        restored = _restore_placeholders(translated_protected, tokens)
        assert restored == "URL von %(site_title)s:"

    def test_roundtrip_multiple(self):
        text = "Transposing by %s semitones: %s"
        protected, tokens = _protect_placeholders(text)
        translated_protected = protected.replace("Transposing by", "Transponierung um").replace(
            "semitones:", "Halbtone:"
        )
        restored = _restore_placeholders(translated_protected, tokens)
        assert restored == "Transponierung um %s Halbtone: %s"

    @pytest.mark.parametrize(
        "source",
        [
            "Downloaded: %s",
            "Download queued (#%d): %s",
            "URL of %(site_title)s:",
            "Error renaming file: '%s' to '%s', %s",
            "%s added to top of queue: %s",
            "Added %s random tracks",
        ],
        ids=[
            "single_%s",
            "mixed_%d_%s",
            "named_placeholder",
            "three_placeholders",
            "leading_%s",
            "mid_sentence_%s",
        ],
    )
    def test_roundtrip_real_strings(self, source):
        """Verify that protect -> restore is lossless for real PiKaraoke strings."""
        protected, tokens = _protect_placeholders(source)
        restored = _restore_placeholders(protected, tokens)
        assert restored == source


class TestValidatePlaceholders:
    def test_valid_translation(self):
        assert _validate_placeholders("Downloaded: %s", "Heruntergeladen: %s")

    def test_missing_placeholder(self):
        assert not _validate_placeholders("Downloaded: %s", "Heruntergeladen:")

    def test_multiple_valid(self):
        source = "Transposing by %s semitones: %s"
        translated = "%s 半音ずつ移調します: %s"
        assert _validate_placeholders(source, translated)

    def test_named_placeholder_valid(self):
        assert _validate_placeholders("URL of %(site_title)s:", "URL von %(site_title)s:")

    def test_named_placeholder_missing(self):
        assert not _validate_placeholders("URL of %(site_title)s:", "URL von:")

    def test_html_valid(self):
        source = "<b>bold</b>"
        translated = "<b>fett</b>"
        assert _validate_placeholders(source, translated)

    def test_html_missing(self):
        assert not _validate_placeholders("<b>bold</b>", "fett")

    def test_no_placeholders(self):
        assert _validate_placeholders("Hello", "Hallo")

    def test_duplicate_placeholder_both_present(self):
        source = "%s added to queue: %s"
        translated = "%s zur Warteschlange hinzugefugt: %s"
        assert _validate_placeholders(source, translated)

    def test_duplicate_placeholder_one_dropped(self):
        source = "%s added to queue: %s"
        translated = "zur Warteschlange hinzugefugt: %s"
        assert not _validate_placeholders(source, translated)

    def test_three_placeholders_one_dropped(self):
        source = "Error renaming file: '%s' to '%s', %s"
        translated = "Fehler beim Umbenennen: '%s' nach '%s'"
        assert not _validate_placeholders(source, translated)


class TestBraceTokens:
    """Tokens the call site fills in with replace() must survive translation verbatim."""

    def test_protected(self):
        protected, tokens = _protect_placeholders("{start}-{end} of {total}")
        assert tokens == ["{start}", "{end}", "{total}"]
        assert protected == "<x0>-<x1> of <x2>"

    def test_roundtrip(self):
        text = "Current session: {name}"
        assert _restore_placeholders(*_protect_placeholders(text)) == text

    def test_translated_token_rejected(self):
        assert not _validate_placeholders("{start}-{end} of {total}", "ANFANG-ENDE von GESAMT")

    def test_preserved_token_accepted(self):
        assert _validate_placeholders("{start}-{end} of {total}", "{start}-{end} von {total}")

    def test_uppercase_word_is_not_a_token(self):
        """ENTIRE is emphasis, not a placeholder, so the translator should see it."""
        assert _protect_placeholders("clear the ENTIRE queue")[1] == []


class TestTranslateOnce:
    def test_succeeds_first_try(self):
        translator = FakeTranslator()
        assert _translate_once("hello", translator) == "translated hello"
        assert translator.calls == 1

    def test_retries_transient_error(self):
        translator = FakeTranslator(failures=1)
        assert _translate_once("hello", translator) == "translated hello"
        assert translator.calls == 2

    def test_gives_up_after_one_retry(self):
        translator = FakeTranslator(failures=2)
        with pytest.raises(TranslationNotFound):
            _translate_once("hello", translator)
        assert translator.calls == 2

    def test_permanent_error_is_not_retried(self):
        translator = FakeTranslator(failures=1, error=LanguageNotSupportedException)
        with pytest.raises(LanguageNotSupportedException):
            _translate_once("hello", translator)
        assert translator.calls == 1


class TestTranslateEntry:
    def test_recovers_from_transient_error(self):
        entry = polib.POEntry(msgid="Session {number}")
        assert translate_entry(entry, FakeTranslator(failures=1)) == "translated Session {number}"

    def test_returns_none_when_translation_fails(self):
        entry = polib.POEntry(msgid="Session {number}")
        assert translate_entry(entry, FakeTranslator(failures=2)) is None

    def test_skips_blank_msgid(self):
        translator = FakeTranslator()
        assert translate_entry(polib.POEntry(msgid="   "), translator) is None
        assert translator.calls == 0
