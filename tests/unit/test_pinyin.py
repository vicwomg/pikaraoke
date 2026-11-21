"""Tests for the pinyin readings behind the A-Z strip and the sort order."""

from pikaraoke.lib.pinyin import han_to_pinyin, pinyin_initial


class TestPinyinInitial:
    def test_simplified_title(self):
        assert pinyin_initial("稻香") == "d"

    def test_traditional_title(self):
        assert pinyin_initial("周杰倫") == "z"

    def test_polyphone_read_in_phrase_context(self):
        """重庆 is Chongqing, so the first syllable is C, not 重's default Z."""
        assert pinyin_initial("重庆森林") == "c"

    def test_lone_polyphone_uses_its_default_reading(self):
        assert pinyin_initial("重") == "z"

    def test_non_han_text_has_no_pinyin_initial(self):
        assert pinyin_initial("Abba - Waterloo") is None

    def test_empty_text(self):
        assert pinyin_initial("") is None

    def test_punctuation_before_han_has_no_initial(self):
        """The bar keys on the filename's first character, whatever it is."""
        assert pinyin_initial("《稻香》") is None


class TestHanToPinyin:
    def test_replaces_han_with_pinyin(self):
        assert han_to_pinyin("稻香") == "daoxiang"

    def test_polyphone_read_in_phrase_context(self):
        assert han_to_pinyin("重庆森林") == "chongqingsenlin"

    def test_mixed_text_keeps_latin_and_punctuation(self):
        assert han_to_pinyin("周杰伦 - 稻香") == "zhoujielun - daoxiang"

    def test_text_without_han_is_untouched(self):
        assert han_to_pinyin("99 Luftballons - Abba") == "99 Luftballons - Abba"
