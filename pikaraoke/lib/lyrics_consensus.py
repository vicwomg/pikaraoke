"""Multi-source lyrics consensus.

Cross-validates 6 lyrics sources (VTT, Whisper, LRCLib, Musixmatch, Megalobiz,
Genius) against an audio reference (VTT + Whisper tokens) before allowing the
T3 word-level ASS overwrite. Defends against the wrong-version overwrite class
of bug, where one fetcher silently returns a different song's lyrics that
survive the existing tier gate (which validates only sync quality, not content).

Pattern mirrors :mod:`pikaraoke.lib.lyrics_language_classifier` — collect ->
score -> consensus -> persist. No literal code reuse: tokens vs language codes
are different domains.

State machine::

    IDLE -> COLLECTING -> DECIDING ----> ALIGNING -> WRITE T3
                            |
                            +-- confidence < 0.5 -> SKIP (T1/T2 stay on screen)
                            +-- empty consensus  -> SKIP (emit song_warning)

Pure Python, zero I/O. Threading and aligner orchestration live in
:mod:`pikaraoke.lib.lyrics`; this module only computes the consensus.
"""

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikaraoke.lib.lyrics import Word

logger = logging.getLogger(__name__)


_REJECT_THRESHOLDS: dict[str, float] = {
    "lrclib": 0.70,
    "musixmatch": 0.70,
    "megalobiz": 0.70,
    "genius": 0.55,
}
_DEFAULT_THRESHOLD = 0.55

_SCAFFOLD_RANK: dict[str, int] = {
    "lrclib": 0,
    "musixmatch": 1,
    "megalobiz": 2,
    "whisper": 3,
    "vtt": 4,
}

_CONFIDENCE_MIN = 0.5
_CONFIDENCE_PENALTY_NO_AUDIO_REF = 0.7
_CONTIGUITY_MIN = 0.4

_SECTION_HEADER_RE = re.compile(r"\[[^\]]+\]")
_PAREN_RE = re.compile(r"\([^)]*\)")
_PUNCT_RE = re.compile(r"[^\w'\s]", re.UNICODE)


@dataclass
class SourceResult:
    """Output of one fetcher, fed into the consensus pool."""

    name: str  # "vtt" | "whisper" | "lrclib" | "musixmatch" | "megalobiz" | "genius"
    kind: str  # "source_matched" | "title_matched"
    lrc: str | None = None
    plain_text: str | None = None
    words: "list[Word] | None" = None
    is_synced: bool = False


@dataclass
class ConsensusResult:
    """Result of cross-validating sources against the audio reference."""

    text: str
    lrc: str
    sources_used: list[str]
    sources_rejected: list[tuple[str, str]] = field(default_factory=list)
    confidence: float = 0.0


# ---------- Step 1: tokenize ----------


def normalize_tokens(text: str | None) -> list[str]:
    """Lowercase, strip section headers / parens / punctuation, drop tiny tokens.

    Apostrophes preserved so contractions ("it's", "don't") survive.
    Tokens shorter than 2 chars dropped — they are usually OCR noise or
    LRC metadata fragments.
    """
    if not text:
        return []
    s = _SECTION_HEADER_RE.sub(" ", text)
    s = _PAREN_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = s.lower()
    return [t for t in s.split() if len(t) >= 2]


def _source_tokens(source: SourceResult) -> list[str]:
    """Pull a token list from whichever field a source populated."""
    if source.lrc:
        # Strip LRC timestamps before tokenizing.
        body = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", " ", source.lrc)
        return normalize_tokens(body)
    if source.plain_text:
        return normalize_tokens(source.plain_text)
    if source.words:
        return normalize_tokens(" ".join(w.text for w in source.words))
    return []


# ---------- Step 2: audio reference ----------


def build_audio_reference(vtt: SourceResult | None, whisper: SourceResult | None) -> list[str]:
    """Token sequence representing what the audio actually contains.

    VTT tokens (when present) come first since YouTube captions are usually
    closer to lead vocals. Whisper tokens follow to cover anything VTT missed.
    Adjacent identical tokens are collapsed so repeated words at the seam
    don't double-count in the SequenceMatcher comparison.
    """
    tokens: list[str] = []
    if vtt is not None:
        tokens.extend(_source_tokens(vtt))
    if whisper is not None:
        tokens.extend(_source_tokens(whisper))
    out: list[str] = []
    for t in tokens:
        if not out or out[-1] != t:
            out.append(t)
    return out


# ---------- Step 3+4: score + contiguity ----------


def score_against_reference(tokens: list[str], ref: list[str]) -> tuple[float, bool]:
    """Coverage ratio + order_uncertain flag.

    Returns ``(0.0, True)`` when either side is empty. ``order_uncertain``
    fires when the longest matching block is small relative to total
    matched tokens — a permuted-verse cover version, where the source's
    tokens overlap the reference but in scrambled order.
    """
    if not tokens or not ref:
        return 0.0, True
    matcher = SequenceMatcher(None, ref, tokens, autojunk=False)
    coverage = matcher.ratio()
    longest = matcher.find_longest_match(0, len(ref), 0, len(tokens))
    matched = sum(b.size for b in matcher.get_matching_blocks())
    if matched <= 0:
        return coverage, True
    contiguity = longest.size / matched
    return coverage, contiguity < _CONTIGUITY_MIN


def _threshold_for(name: str) -> float:
    return _REJECT_THRESHOLDS.get(name, _DEFAULT_THRESHOLD)


# ---------- Step 5: vote ----------


def vote_tokens(
    audio_ref: list[str], survivors: list[tuple[SourceResult, list[str]]]
) -> dict[int, str]:
    """Majority-vote a token at each audio_ref index.

    Use SequenceMatcher opcodes (not just matching blocks) so sources can
    propose alternative tokens at positions where they disagree with the
    audio reference. ``equal`` opcodes contribute the matched token,
    ``replace`` opcodes align 1:1 within the block so a source can vote
    for its own version of the lyric. ``delete``/``insert`` ranges are
    skipped (one side has no slot to map to).

    Ties broken by source kind: ``source_matched`` (VTT/Whisper, audio
    truth) beats ``title_matched`` (curated lyrics text) on presence —
    better to keep an audio-confirmed token with a minor typo than swap
    in a confident-but-absent word.
    """
    candidates: list[list[tuple[str, str]]] = [[] for _ in audio_ref]
    for source, tokens in survivors:
        if not tokens:
            continue
        matcher = SequenceMatcher(None, audio_ref, tokens, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                for k in range(i2 - i1):
                    candidates[i1 + k].append((tokens[j1 + k], source.kind))
            elif op == "replace":
                n = min(i2 - i1, j2 - j1)
                for k in range(n):
                    candidates[i1 + k].append((tokens[j1 + k], source.kind))

    result: dict[int, str] = {}
    for ref_idx, votes in enumerate(candidates):
        if not votes:
            result[ref_idx] = audio_ref[ref_idx]
            continue
        counts: dict[str, int] = {}
        for tok, _kind in votes:
            counts[tok] = counts.get(tok, 0) + 1
        best_count = max(counts.values())
        winners = [t for t, c in counts.items() if c == best_count]
        if len(winners) == 1:
            result[ref_idx] = winners[0]
            continue
        ranked = []
        for tok in winners:
            kind_rank = min((0 if k == "source_matched" else 1) for t, k in votes if t == tok)
            ranked.append((kind_rank, tok))
        ranked.sort()
        result[ref_idx] = ranked[0][1]
    return result


# ---------- Step 6: scaffold ----------


def select_scaffold(
    survivors: list[SourceResult], order_uncertain: set[str]
) -> SourceResult | None:
    """Pick the source whose timestamps drive the consensus LRC.

    Synced title-matched sources rank highest (LRCLib > MXM > Megalobiz);
    Whisper words rank below those because timestamps are word-level but
    the text may diverge from human-curated. VTT is the last-resort
    line-level scaffold. Order-uncertain sources are excluded — their
    tokens still vote, but their timestamps would mis-place lines.
    """
    eligible: list[SourceResult] = []
    for s in survivors:
        if s.name in order_uncertain:
            continue
        if s.is_synced or s.words:
            eligible.append(s)
    if not eligible:
        # Last resort: VTT line-level scaffold.
        for s in survivors:
            if s.name == "vtt" and s.is_synced:
                return s
        return None
    eligible.sort(key=lambda s: _SCAFFOLD_RANK.get(s.name, 99))
    return eligible[0]


# ---------- Step 7: build LRC ----------


def _parse_lrc_lines(lrc: str) -> list[tuple[str, str]]:
    """Split LRC into (timestamp_prefix, text) tuples preserving order."""
    out: list[tuple[str, str]] = []
    tag_re = re.compile(r"^(\[\d+:\d+(?:\.\d+)?\])(.*)$")
    for raw in lrc.splitlines():
        m = tag_re.match(raw.strip())
        if not m:
            continue
        prefix, text = m.group(1), m.group(2).strip()
        out.append((prefix, text))
    return out


def _index_voted_text(scaffold_text: str, voted: dict[int, str]) -> str:
    """Map a scaffold line's tokens to their voted replacements.

    Returns the joined voted tokens for the line, falling back to the
    scaffold's original line text if the voted slice is empty (e.g. when
    the scaffold has more lines than audio_ref tokens).
    """
    line_tokens = normalize_tokens(scaffold_text)
    if not line_tokens:
        return scaffold_text
    return scaffold_text  # placeholder; real mapping happens in build_consensus_lrc


def build_consensus_lrc(scaffold: SourceResult, voted: dict[int, str]) -> str:
    """Emit a standard LRC string using scaffold timestamps and voted text.

    Empty voted slice for a scaffold line -> fall back to scaffold's own
    line text (Eng review Q3) so we never emit empty Dialogue events.
    """
    if scaffold.lrc:
        lines = _parse_lrc_lines(scaffold.lrc)
    elif scaffold.words:
        lines = []
        for w in scaffold.words:
            mm = int(w.start // 60)
            ss = w.start - mm * 60
            lines.append((f"[{mm:02d}:{ss:05.2f}]", w.text))
    else:
        return ""

    voted_seq = [voted[i] for i in sorted(voted.keys())]
    cursor = 0
    out_lines: list[str] = []
    for prefix, original_text in lines:
        n_tokens = len(normalize_tokens(original_text))
        if n_tokens == 0:
            out_lines.append(f"{prefix}{original_text}")
            continue
        slice_end = min(cursor + n_tokens, len(voted_seq))
        chosen = voted_seq[cursor:slice_end]
        cursor = slice_end
        if not chosen:
            logger.warning(
                "consensus: scaffold line beyond audio_ref end, using original text: %r",
                original_text[:80],
            )
            out_lines.append(f"{prefix}{original_text}")
        else:
            out_lines.append(f"{prefix}{' '.join(chosen)}")
    return "\n".join(out_lines)


# ---------- Step 8: top-level ----------


def build_consensus(sources: list[SourceResult], audio_ref: list[str]) -> ConsensusResult | None:
    """Cross-validate sources against the audio reference.

    Returns None when:
      * all title-matched sources are rejected and no audio-ref-derived
        scaffold survives;
      * the resulting confidence falls below ``_CONFIDENCE_MIN``;
      * no scaffold can be selected.

    The caller treats None as "do not write T3" — T1/T2 stay on screen and
    a song_warning is emitted.
    """
    if not sources:
        return None

    confidence_penalty = 1.0
    if not audio_ref:
        # Empty audio reference: fall back to highest-coverage title-matched
        # as the reference, and penalize confidence to flag the degradation.
        title_matched = [s for s in sources if s.kind == "title_matched"]
        if not title_matched:
            return None
        title_matched.sort(key=lambda s: _SCAFFOLD_RANK.get(s.name, 99))
        audio_ref = _source_tokens(title_matched[0])
        confidence_penalty = _CONFIDENCE_PENALTY_NO_AUDIO_REF
        if not audio_ref:
            return None

    survivors: list[SourceResult] = []
    survivor_tokens: list[tuple[SourceResult, list[str]]] = []
    rejected: list[tuple[str, str]] = []
    order_uncertain: set[str] = set()
    coverages: list[float] = []

    audio_ref_owners = {"vtt", "whisper"}
    for source in sources:
        tokens = _source_tokens(source)
        if source.name in audio_ref_owners:
            survivors.append(source)
            survivor_tokens.append((source, tokens))
            continue
        coverage, uncertain = score_against_reference(tokens, audio_ref)
        threshold = _threshold_for(source.name)
        if coverage < threshold:
            rejected.append((source.name, f"coverage {coverage:.2f} < {threshold:.2f}"))
            continue
        if uncertain:
            order_uncertain.add(source.name)
        survivors.append(source)
        survivor_tokens.append((source, tokens))
        coverages.append(coverage)

    if not survivors:
        return None

    scaffold = select_scaffold(survivors, order_uncertain)
    if scaffold is None:
        return None

    voted = vote_tokens(audio_ref, survivor_tokens)
    voted_text = " ".join(voted[i] for i in sorted(voted.keys()))
    lrc_out = build_consensus_lrc(scaffold, voted)
    if not lrc_out:
        return None

    audio_ref_present = any(s.name in ("vtt", "whisper") for s in survivors)
    title_total = sum(1 for s in sources if s.kind == "title_matched")
    title_surviving = sum(1 for s in survivors if s.kind == "title_matched")
    title_rate = title_surviving / title_total if title_total else 1.0
    title_cov = sum(coverages) / len(coverages) if coverages else 1.0
    base = 0.6 if audio_ref_present else 0.0
    confidence = (base + (1.0 - base) * title_rate * title_cov) * confidence_penalty
    if confidence < _CONFIDENCE_MIN:
        logger.info(
            "consensus: confidence %.2f below %.2f gate, skipping T3",
            confidence,
            _CONFIDENCE_MIN,
        )
        return None

    return ConsensusResult(
        text=voted_text,
        lrc=lrc_out,
        sources_used=[s.name for s in survivors],
        sources_rejected=rejected,
        confidence=confidence,
    )
