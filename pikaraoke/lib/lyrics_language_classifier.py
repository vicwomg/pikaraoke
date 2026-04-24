"""Tier 1 text-consensus language classifier for the lyrics pipeline (US-43).

Motivation: before this module, every lyrics-path language write landed
under ``scanner`` provenance. A single mislabelled LRCLib record (the
Kolorowy wiatr case: Polish metadata, English lyrics text) would then
poison ``songs.language='en'`` on a cold-DB first run, defeating the
``ab066fef`` dub-trap guard on every subsequent run.

This classifier collects up to eight language signals from data
pikaraoke already fetches — yt-dlp info.json, the cached iTunes search
response, the cached MusicBrainz recording search, and langdetect on
DB-stored title/artist fields. A consensus rule (>=2 signals agreeing
on the same primary subtag) decides the verdict; the verdict is then
persisted to ``songs.language`` under the highest-ranked agreeing
source's provenance rung. Disagreement (no subtag with >=2 votes)
leaves the DB alone — that's the Tier 2 Whisper probe's job (future
commit), and writing every signal independently would let a noisy
source (iTunes collection names mixing English boilerplate into Polish
albums) outrank a clean one (MusicBrainz release-country aggregates)
purely on rung order.

Design constraints (from US-43):
  - No new HTTP fetches. Every signal derives from a response
    pikaraoke already pulls today; the classifier only widens the
    parsing layer.
  - Tier 1 must run at ``song_downloaded`` time, not after
    ``stems_ready`` — the line-level fast-path ``.ass`` has to appear
    before demucs finishes.
  - Budget: <= 50ms per song in the happy path.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field

from pikaraoke.lib.karaoke_database import KaraokeDatabase

logger = logging.getLogger(__name__)

# Minimum characters for langdetect to attempt classification. Short
# strings (a single word, a two-word song title) classify wrong too
# often to be useful as a language signal.
_LANGDETECT_MIN_CHARS = 12

# Coarse country-code -> primary language subtag fallback used for the
# weakest tiebreaker rungs (``itunes_country`` and ``mb_release_country``).
# Kept intentionally short: storefront country is a lossy hint, and a
# wrong map entry would be worse than no entry. Multi-language countries
# (Canada, Belgium, Switzerland, India, ...) are deliberately absent —
# the classifier will just not emit a signal for them.
_COUNTRY_TO_LANG: dict[str, str] = {
    "POL": "pl",
    "PL": "pl",
    "USA": "en",
    "US": "en",
    "GBR": "en",
    "GB": "en",
    "AUS": "en",
    "CAN": "en",  # leans English; French-Canadian is a known ambiguity
    "IRL": "en",
    "NZL": "en",
    "ZAF": "en",
    "DEU": "de",
    "DE": "de",
    "AUT": "de",
    "AT": "de",
    "FRA": "fr",
    "FR": "fr",
    "ESP": "es",
    "ES": "es",
    "MEX": "es",
    "ARG": "es",
    "ITA": "it",
    "IT": "it",
    "PRT": "pt",
    "PT": "pt",
    "BRA": "pt",
    "BR": "pt",
    "RUS": "ru",
    "RU": "ru",
    "UKR": "uk",
    "UA": "uk",
    "JPN": "ja",
    "JP": "ja",
    "KOR": "ko",
    "KR": "ko",
    "CHN": "zh",
    "CN": "zh",
    "TWN": "zh",
    "TW": "zh",
    "NLD": "nl",
    "NL": "nl",
    "SWE": "sv",
    "SE": "sv",
    "NOR": "no",
    "NO": "no",
    "DNK": "da",
    "DK": "da",
    "FIN": "fi",
    "FI": "fi",
    "CZE": "cs",
    "CZ": "cs",
    "HUN": "hu",
    "HU": "hu",
    "GRC": "el",
    "GR": "el",
    "TUR": "tr",
    "TR": "tr",
}


@dataclass(frozen=True)
class LanguageSignal:
    """One verdict contributed by one named source.

    ``source`` is a rung name registered in ``METADATA_SOURCE_CONFIDENCE``.
    ``language`` is always a normalized primary subtag (``pl``, ``en``,
    ``pt``; never ``pl-PL`` or ``en-US``). ``detail`` is a short
    human-readable hint shown in logs so operators can tell at a glance
    *why* a given source voted the way it did.
    """

    source: str
    language: str
    detail: str


@dataclass(frozen=True)
class ConsensusVerdict:
    """Aggregate of signals that agreed on the same primary subtag."""

    language: str
    agreement: int
    winning_source: str
    signals: tuple[LanguageSignal, ...] = field(default_factory=tuple)


def _lang_base(lang: str | None) -> str:
    if not lang:
        return ""
    return lang.split("-", 1)[0].split("_", 1)[0].lower()


def _langdetect(text: str) -> str | None:
    """Thin, lazy-import wrapper around ``langdetect.detect``.

    Kept separate from ``pikaraoke.lib.lyrics._detect_language`` so this
    module does not need the whole lyrics.py import chain for tests that
    only exercise consensus logic.
    """
    text = (text or "").strip()
    if len(text) < _LANGDETECT_MIN_CHARS:
        return None
    try:
        import langdetect
    except ImportError:
        return None
    langdetect.DetectorFactory.seed = 0
    try:
        return langdetect.detect(text)
    except Exception:
        return None


# --- per-source signal extractors --------------------------------------


# Explicit dub/version markers that appear in iTunes collection names.
# Maps English language name -> primary subtag; we scan for
# ``<name> (ver|version|edition|dub|language)`` and a handful of native
# equivalents for languages whose iTunes storefronts commonly use them.
# Conservative by design — only unambiguous markers, never just the bare
# language name (``"Polish"`` alone could easily appear in an English
# album title without meaning the lyrics are Polish).
_LANG_NAME_TO_CODE = {
    "polish": "pl",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "czech": "cs",
    "hungarian": "hu",
    "dutch": "nl",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "mandarin": "zh",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "greek": "el",
    "turkish": "tr",
    "ukrainian": "uk",
}
_DUB_MARKER_EN = re.compile(
    r"\b(" + "|".join(_LANG_NAME_TO_CODE) + r")\s+(ver|version|edition|dub|language)\b",
    re.IGNORECASE,
)
# Native-language dub markers. Keyed by primary subtag; the regex fires
# on the raw (case-insensitive) text.
_DUB_MARKERS_NATIVE: tuple[tuple[str, re.Pattern], ...] = (
    ("pl", re.compile(r"\b(polska\s+wersja|wersja\s+polska)\b", re.IGNORECASE)),
    ("de", re.compile(r"\b(deutsche\s+version)\b", re.IGNORECASE)),
    ("fr", re.compile(r"\b(version\s+fran[cç]aise)\b", re.IGNORECASE)),
    ("es", re.compile(r"\b(versi[oó]n\s+espa[ñn]ola)\b", re.IGNORECASE)),
    ("it", re.compile(r"\b(versione\s+italiana)\b", re.IGNORECASE)),
)


def _dub_hint_language(text: str) -> tuple[str, str] | None:
    """Scan ``text`` for a dub/version marker; return ``(lang, matched)`` or None.

    Catches the "Polish Ver" / "Polska Wersja" case in iTunes collection
    names where langdetect would otherwise read the surrounding English
    soundtrack boilerplate and mis-classify the album as English.
    """
    m = _DUB_MARKER_EN.search(text)
    if m:
        return _LANG_NAME_TO_CODE[m.group(1).lower()], m.group(0)
    for code, pattern in _DUB_MARKERS_NATIVE:
        m = pattern.search(text)
        if m:
            return code, m.group(0)
    return None


def _signal_itunes_text(itunes_hit: dict | None) -> LanguageSignal | None:
    """Rung 17. Dub-marker / langdetect over iTunes collection + track + artist."""
    if not itunes_hit:
        return None
    parts = [
        (itunes_hit.get("collectionName") or "").strip(),
        (itunes_hit.get("trackName") or "").strip(),
        (itunes_hit.get("artistName") or "").strip(),
    ]
    text = " ".join(p for p in parts if p)
    # Explicit dub markers beat langdetect — they're unambiguous ground
    # truth when present, and langdetect is what mis-classified these
    # cases in the first place (the Pocahontas "[Polish Ver]" trap).
    hint = _dub_hint_language(text)
    if hint:
        lang, matched = hint
        return LanguageSignal(
            source="itunes_text",
            language=lang,
            detail=f"dub_marker({matched!r})",
        )
    lang = _lang_base(_langdetect(text))
    if not lang:
        return None
    return LanguageSignal(
        source="itunes_text",
        language=lang,
        detail=f"langdetect({text!r:.80s})",
    )


def _signal_itunes_country(itunes_hit: dict | None) -> LanguageSignal | None:
    """Rung 10. Weak tiebreaker — storefront country mapped to a language."""
    if not itunes_hit:
        return None
    country = (itunes_hit.get("country") or "").upper()
    if not country:
        return None
    lang = _COUNTRY_TO_LANG.get(country)
    if not lang:
        return None
    return LanguageSignal(
        source="itunes_country",
        language=lang,
        detail=f"country={country}",
    )


def _signal_mb_release_titles(mb_signals: dict | None) -> LanguageSignal | None:
    """Rung 16. langdetect over MB release titles, voting per-release.

    MB compilations frequently mix languages ("Best Of Disney | Złota
    kolekcja"). Running langdetect on the joined string is noisy — the
    English boilerplate outweighs the Polish release title. Splitting on
    the stable ``" | "`` separator used by ``music_metadata`` lets us
    vote per-release; tied or single-title inputs fall back to the joined
    string so we stay conservative on low-data cases.
    """
    if not mb_signals:
        return None
    joined = (mb_signals.get("release_titles_joined") or "").strip()
    if not joined:
        return None
    titles = [t.strip() for t in joined.split(" | ") if t.strip()]
    if len(titles) >= 2:
        votes: dict[str, int] = {}
        for title in titles:
            lang = _lang_base(_langdetect(title))
            if lang:
                votes[lang] = votes.get(lang, 0) + 1
        if votes:
            top = max(votes.values())
            winners = [lang for lang, count in votes.items() if count == top]
            if len(winners) == 1:
                return LanguageSignal(
                    source="mb_release_titles",
                    language=winners[0],
                    detail=f"per_release_vote({votes})",
                )
            # Tie across languages — fall through to the joined heuristic
            # rather than picking arbitrarily.
    lang = _lang_base(_langdetect(joined))
    if not lang:
        return None
    return LanguageSignal(
        source="mb_release_titles",
        language=lang,
        detail=f"langdetect({joined!r:.80s})",
    )


def _signal_mb_release_country(mb_signals: dict | None) -> LanguageSignal | None:
    """Rung 11. Weak tiebreaker — unanimous release country across MB releases."""
    if not mb_signals:
        return None
    countries = mb_signals.get("release_countries") or ()
    if not countries:
        return None
    # Only fire when every release agrees. Mixed-country release lists are
    # too ambiguous to trust.
    unique = set(countries)
    if len(unique) != 1:
        return None
    country = next(iter(unique))
    lang = _COUNTRY_TO_LANG.get(country)
    if not lang:
        return None
    return LanguageSignal(
        source="mb_release_country",
        language=lang,
        detail=f"country={country} ({len(countries)} release{'s' if len(countries) != 1 else ''})",
    )


def _signal_yt_info_lang(yt_info: dict | None) -> LanguageSignal | None:
    """Rung 14. yt-dlp info.json ``language`` / ``original_language`` field."""
    if not yt_info:
        return None
    lang = _lang_base(yt_info.get("language") or yt_info.get("original_language"))
    if not lang:
        return None
    return LanguageSignal(
        source="yt_info_lang",
        language=lang,
        detail=f"info.json language={lang}",
    )


def _signal_yt_subtitle_lang(yt_info: dict | None) -> LanguageSignal | None:
    """Rung 18. yt-dlp info.json manual-subtitle language keys.

    Only manual subtitles count — auto-generated captions inherit the
    model's language guess and are as untrustworthy as a Whisper ASR
    language-ID on a noisy clip. A unanimous vote across manual tracks
    is a strong signal; mixed tracks aren't useful without more logic.
    """
    if not yt_info:
        return None
    subs = yt_info.get("subtitles") or {}
    if not isinstance(subs, dict) or not subs:
        return None
    langs = {_lang_base(k) for k in subs.keys() if k}
    langs.discard("")
    if len(langs) != 1:
        return None
    lang = next(iter(langs))
    return LanguageSignal(
        source="yt_subtitle_lang",
        language=lang,
        detail=f"subtitles={sorted(subs.keys())}",
    )


def _signal_yt_title_lang(yt_info: dict | None) -> LanguageSignal | None:
    """Rung 13. langdetect on the raw yt-dlp video title.

    Pre-canonicalization title, diacritics intact — useful because the
    DB-stored title has often been scrubbed by iTunes canonicalization,
    losing the very diacritics (``ś``, ``ż``, ``ę``) langdetect needs.
    """
    if not yt_info:
        return None
    title = (yt_info.get("title") or "").strip()
    lang = _lang_base(_langdetect(title))
    if not lang:
        return None
    return LanguageSignal(
        source="yt_title_lang",
        language=lang,
        detail=f"langdetect({title!r:.80s})",
    )


def _signal_title_heuristic(db_title: str | None, db_artist: str | None) -> LanguageSignal | None:
    """Rung 12. langdetect on the DB-stored title + artist.

    The weakest text heuristic — DB fields are often iTunes-canonicalized
    and may have lost diacritics. Still useful as a last-resort signal
    when no other source fired.
    """
    text = f"{(db_title or '').strip()} {(db_artist or '').strip()}".strip()
    lang = _lang_base(_langdetect(text))
    if not lang:
        return None
    return LanguageSignal(
        source="title_heuristic",
        language=lang,
        detail=f"langdetect({text!r:.80s})",
    )


# --- public entry points ------------------------------------------------


def read_info_json(song_path: str) -> dict | None:
    """Return the parsed ``<stem>.info.json`` payload or None.

    The info.json is consumed-then-deleted by ``song_manager.register_download``,
    so this only fires during the short window between yt-dlp finishing and
    that consume. Callers that need yt language signals after consume must
    capture them ahead of time.
    """
    info_path = f"{os.path.splitext(song_path)[0]}.info.json"
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read %s: %s", info_path, e)
        return None


def collect_signals(
    *,
    yt_info: dict | None = None,
    itunes_hit: dict | None = None,
    mb_signals: dict | None = None,
    db_title: str | None = None,
    db_artist: str | None = None,
) -> list[LanguageSignal]:
    """Run every Tier 1 extractor and return the non-null signals.

    Any extractor is free to return None; missing inputs (no iTunes hit,
    no info.json) just mean fewer signals. Order in the output list is
    insertion order (stable, extractor-declared) — callers should not
    depend on it for persistence ordering, the per-rung ladder handles
    that.
    """
    extractors = (
        _signal_yt_info_lang(yt_info),
        _signal_yt_subtitle_lang(yt_info),
        _signal_yt_title_lang(yt_info),
        _signal_itunes_text(itunes_hit),
        _signal_itunes_country(itunes_hit),
        _signal_mb_release_titles(mb_signals),
        _signal_mb_release_country(mb_signals),
        _signal_title_heuristic(db_title, db_artist),
    )
    return [s for s in extractors if s is not None]


def consensus(signals: list[LanguageSignal], min_agreeing: int = 2) -> ConsensusVerdict | None:
    """Return the primary subtag with the most agreeing signals.

    ``min_agreeing`` defaults to 2 per the US-43 design doc: a single
    signal is tentative and does not establish consensus. Ties go to the
    rung order declared by ``METADATA_SOURCE_CONFIDENCE`` (highest
    rung wins within the agreement cohort), so a later strong signal
    (``whisper_asr`` voting with ``itunes_text``) picks the right
    provenance tag for the winner.
    """
    from collections import Counter

    from pikaraoke.lib.karaoke_database import METADATA_SOURCE_CONFIDENCE

    counts = Counter(s.language for s in signals)
    if not counts:
        return None
    top_lang, top_count = counts.most_common(1)[0]
    if top_count < min_agreeing:
        return None
    agreeing = [s for s in signals if s.language == top_lang]
    # Tie-break winning source by ladder rung (highest).
    winner = max(agreeing, key=lambda s: METADATA_SOURCE_CONFIDENCE.get(s.source, 0))
    return ConsensusVerdict(
        language=top_lang,
        agreement=top_count,
        winning_source=winner.source,
        signals=tuple(agreeing),
    )


def classify_and_persist(
    db: KaraokeDatabase,
    song_id: int,
    *,
    song_path: str | None = None,
    yt_info: dict | None = None,
    itunes_hit: dict | None = None,
    mb_signals: dict | None = None,
    db_title: str | None = None,
    db_artist: str | None = None,
) -> tuple[list[LanguageSignal], ConsensusVerdict | None]:
    """Collect signals, apply the consensus rule, persist the winner.

    Writes at most one row to ``songs.language``, using the
    highest-ranked source inside the agreeing cohort as the provenance
    rung. Disagreement (no subtag with >=2 votes) leaves the DB alone
    and falls through to Tier 2 per the US-43 design doc — writing every
    signal independently would let a mislabelled iTunes collectionName
    beat a cleaner MusicBrainz release-country aggregate purely on rung
    order, which is exactly the failure mode the consensus rule is there
    to prevent.

    All collected signals (not just the winner) are logged for operator
    observability — a later Tier-2 Whisper probe can correlate against
    the Tier-1 signal trail when diagnosing a wrong verdict.
    """
    signals = collect_signals(
        yt_info=yt_info,
        itunes_hit=itunes_hit,
        mb_signals=mb_signals,
        db_title=db_title,
        db_artist=db_artist,
    )
    tag = os.path.basename(song_path) if song_path else f"song_id={song_id}"
    for sig in signals:
        logger.info(
            "US-43 signal: %s source=%s lang=%s detail=%s",
            tag,
            sig.source,
            sig.language,
            sig.detail,
        )
    if not signals:
        logger.info("US-43 classifier: no signals for %s", tag)
        return signals, None

    verdict = consensus(signals)
    if verdict is None:
        counts: dict[str, int] = {}
        for s in signals:
            counts[s.language] = counts.get(s.language, 0) + 1
        logger.info(
            "US-43 no-consensus: %s signals=%d votes=%s (DB unchanged, "
            "awaiting Tier 2 Whisper probe)",
            tag,
            len(signals),
            counts,
        )
        return signals, None

    try:
        applied = db.update_track_metadata_with_provenance(
            song_id,
            verdict.winning_source,
            {"language": verdict.language},
        )
    except Exception:
        logger.exception(
            "US-43 classifier: failed to persist consensus verdict %s for %s",
            verdict,
            tag,
        )
        return signals, verdict
    logger.info(
        "US-43 consensus: %s lang=%s agreement=%d/%d winning_source=%s " "applied=%s sources=%s",
        tag,
        verdict.language,
        verdict.agreement,
        len(signals),
        verdict.winning_source,
        bool(applied),
        [s.source for s in signals],
    )
    return signals, verdict
