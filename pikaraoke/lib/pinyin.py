"""Pinyin readings, so Chinese titles join the A-Z strip and its sort order.

Without this a Han character lights no slot on the bar, so a Chinese library
renders a strip that is entirely grey and cannot be jumped with, and every
Chinese title piles up after Z instead of sitting with its pronunciation.
Filing 稻香 under D and 重庆 under C gives those songs the same section and the
same ordering as Latin ones.
"""

from functools import lru_cache

# pypinyin's longest phrase is shorter than this, so the window carries enough
# context to read a polyphone at the head of a title in its phrase's reading.
_PHRASE_WINDOW = 8

_HAN_RANGES = (
    (0x3400, 0x4DBF),  # Extension A
    (0x4E00, 0x9FFF),  # Unified Ideographs
    (0xF900, 0xFAFF),  # Compatibility Ideographs
    (0x20000, 0x2FA1F),  # Extensions B-F and Compatibility Supplement
)


def _is_han(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _HAN_RANGES)


@lru_cache(maxsize=4096)
def pinyin_initial(text: str) -> str | None:
    """Lowercase pinyin initial of `text`'s leading Han character, or None.

    Reads the leading phrase rather than the one character so polyphones come out
    right: 重庆 is C, while 重 on its own is Z. Imported inside the function
    because pypinyin spends a third of a second loading its dictionaries, which a
    Latin-only library should not pay at startup.
    """
    if not text or not _is_han(text[0]):
        return None
    from pypinyin import Style, lazy_pinyin

    syllables = lazy_pinyin(text[:_PHRASE_WINDOW], style=Style.NORMAL, errors="ignore")
    if not syllables or not syllables[0] or not syllables[0][0].isalpha():
        return None
    return syllables[0][0].lower()


@lru_cache(maxsize=8192)
def han_to_pinyin(text: str) -> str:
    """Replace every Han character in `text` with its pinyin, keeping the rest.

    The sort key's transliteration, so 稻香 sorts as "daoxiang" next to Latin
    titles rather than after Z. The whole string is read at once so polyphones
    resolve in phrase context. Text without Han characters comes back untouched,
    which is also what keeps pypinyin out of a Latin-only library's startup.
    """
    if not any(_is_han(char) for char in text):
        return text
    from pypinyin import lazy_pinyin

    return "".join(lazy_pinyin(text, errors="default"))
