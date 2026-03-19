"""Unit tests for placeholder protection in update_translations."""

import pytest

pytest.importorskip("polib", reason="polib is only in the 'translations' dependency group")

from build_scripts.update_translations import (
    _protect_placeholders,
    _restore_placeholders,
    _validate_placeholders,
)


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
