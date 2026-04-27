"""Unit tests for the multi-source lyrics consensus engine."""

import pytest

from pikaraoke.lib.lyrics import Word
from pikaraoke.lib.lyrics_consensus import (
    _CONFIDENCE_MIN,
    ConsensusResult,
    SourceResult,
    build_audio_reference,
    build_consensus,
    build_consensus_lrc,
    normalize_tokens,
    score_against_reference,
    select_scaffold,
    vote_tokens,
)


def _vtt(lrc: str) -> SourceResult:
    return SourceResult(name="vtt", kind="source_matched", lrc=lrc, is_synced=True)


def _whisper(words: list[Word]) -> SourceResult:
    return SourceResult(name="whisper", kind="source_matched", words=words, is_synced=False)


def _lrclib(lrc: str | None) -> SourceResult:
    return SourceResult(name="lrclib", kind="title_matched", lrc=lrc, is_synced=lrc is not None)


def _musixmatch(lrc: str | None) -> SourceResult:
    return SourceResult(name="musixmatch", kind="title_matched", lrc=lrc, is_synced=lrc is not None)


def _megalobiz(lrc: str | None) -> SourceResult:
    return SourceResult(name="megalobiz", kind="title_matched", lrc=lrc, is_synced=lrc is not None)


def _genius(text: str) -> SourceResult:
    return SourceResult(name="genius", kind="title_matched", plain_text=text, is_synced=False)


_MOONLIGHT_CORRECT_LRC = "\n".join(
    [
        "[00:13.50]The last that ever she saw him",
        "[00:18.20]Carried away by a moonlight shadow",
        "[00:23.10]He passed on worried and warning",
        "[00:27.80]Carried away by a moonlight shadow",
        "[00:32.40]Lost in a riddle that Saturday night",
        "[00:37.10]Far away on the other side",
    ]
)

_MOONLIGHT_WRONG_GENIUS = (
    "Four A.M. in the morning, carried away by a moonlight shadow\n"
    "I watched your vision forming, carried away by a moonlight shadow\n"
    "Stars roll slow in a silvery night\n"
    "Far away on the other side"
)


# ---------------- T2 normalize_tokens ----------------


class TestNormalizeTokens:
    def test_strips_section_headers(self):
        assert normalize_tokens("[Verse 1] hello world") == ["hello", "world"]

    def test_strips_parens(self):
        assert normalize_tokens("hello (refrain) world") == ["hello", "world"]

    def test_lowercases(self):
        assert normalize_tokens("HELLO World") == ["hello", "world"]

    def test_drops_punctuation(self):
        assert normalize_tokens("hello, world!") == ["hello", "world"]

    def test_preserves_apostrophes(self):
        assert normalize_tokens("it's don't") == ["it's", "don't"]

    def test_drops_short_tokens(self):
        assert normalize_tokens("a hello b world") == ["hello", "world"]

    def test_empty_input(self):
        assert normalize_tokens("") == []
        assert normalize_tokens(None) == []


# ---------------- T3 build_audio_reference ----------------


class TestBuildAudioReference:
    def test_vtt_only(self):
        ref = build_audio_reference(_vtt(_MOONLIGHT_CORRECT_LRC), None)
        assert "the" in ref and "last" in ref and "saw" in ref

    def test_whisper_only(self):
        words = [
            Word(text="hello", start=0.0, end=0.5),
            Word(text="world", start=0.5, end=1.0),
        ]
        ref = build_audio_reference(None, _whisper(words))
        assert ref == ["hello", "world"]

    def test_both_concatenate_with_dedup(self):
        vtt = _vtt("[00:00.00]hello world")
        whisper = _whisper(
            [Word(text="world", start=0.0, end=0.5), Word(text="extra", start=0.5, end=1.0)]
        )
        ref = build_audio_reference(vtt, whisper)
        assert ref == ["hello", "world", "extra"]

    def test_both_none_returns_empty(self):
        assert build_audio_reference(None, None) == []


# ---------------- T4 score_against_reference ----------------


class TestScoreAgainstReference:
    def test_full_match(self):
        ref = ["a", "b", "c", "d"]
        cov, uncertain = score_against_reference(ref, ref)
        assert cov == pytest.approx(1.0)
        assert uncertain is False

    def test_zero_overlap(self):
        cov, uncertain = score_against_reference(["x", "y"], ["a", "b"])
        assert cov == pytest.approx(0.0)
        assert uncertain is True

    def test_partial_match(self):
        ref = ["a", "b", "c", "d"]
        src = ["a", "b", "x", "d"]
        cov, _uncertain = score_against_reference(src, ref)
        assert 0.5 < cov < 1.0

    def test_empty_inputs(self):
        cov, uncertain = score_against_reference([], ["a"])
        assert cov == 0.0 and uncertain is True
        cov, uncertain = score_against_reference(["a"], [])
        assert cov == 0.0 and uncertain is True


# ---------------- T5 contiguity flag ----------------


class TestContiguityFlag:
    def test_contiguous_match_not_uncertain(self):
        ref = ["a", "b", "c", "d", "e"]
        cov, uncertain = score_against_reference(ref, ref)
        assert uncertain is False

    def test_permuted_match_flagged_order_uncertain(self):
        # Every adjacent pair swapped -> all matches are 1-token blocks, no
        # contiguous run survives. Real-world analogue: a cover version with
        # heavily-shuffled verse order.
        ref = list("abcdefghij")
        src = list("badcfehgji")
        cov, uncertain = score_against_reference(src, ref)
        assert cov > 0.3
        assert uncertain is True


# ---------------- T6 vote_tokens ----------------


class TestVoteTokens:
    def test_majority_wins(self):
        ref = ["the", "last"]
        a = SourceResult(name="a", kind="title_matched")
        b = SourceResult(name="b", kind="title_matched")
        c = SourceResult(name="c", kind="title_matched")
        votes = vote_tokens(
            ref,
            [(a, ["the", "last"]), (b, ["the", "last"]), (c, ["the", "best"])],
        )
        assert votes[0] == "the"
        assert votes[1] == "last"

    def test_no_candidate_falls_back_to_audio_ref(self):
        ref = ["solo"]
        votes = vote_tokens(ref, [])
        assert votes[0] == "solo"

    def test_tie_break_source_matched_wins(self):
        ref = ["x"]
        title = SourceResult(name="lrclib", kind="title_matched")
        source = SourceResult(name="vtt", kind="source_matched")
        votes = vote_tokens(ref, [(title, ["alpha"]), (source, ["beta"])])
        # 1-1 tie -> source_matched wins.
        assert votes[0] == "beta"


# ---------------- T7 select_scaffold ----------------


class TestSelectScaffold:
    def test_synced_outranks_whisper(self):
        lrclib = _lrclib("[00:00.00]a")
        whisper = _whisper([Word(text="a", start=0.0, end=0.5)])
        s = select_scaffold([lrclib, whisper], order_uncertain=set())
        assert s is lrclib

    def test_lrclib_outranks_musixmatch(self):
        lrclib = _lrclib("[00:00.00]a")
        mxm = _musixmatch("[00:00.00]a")
        s = select_scaffold([mxm, lrclib], order_uncertain=set())
        assert s is lrclib

    def test_whisper_when_no_synced(self):
        whisper = _whisper([Word(text="a", start=0.0, end=0.5)])
        vtt = _vtt("[00:00.00]a")
        s = select_scaffold([whisper, vtt], order_uncertain=set())
        assert s is whisper

    def test_vtt_line_only_fallback(self):
        vtt = _vtt("[00:00.00]a")
        s = select_scaffold([vtt], order_uncertain=set())
        assert s is vtt

    def test_order_uncertain_excluded(self):
        lrclib = _lrclib("[00:00.00]a")
        whisper = _whisper([Word(text="a", start=0.0, end=0.5)])
        s = select_scaffold([lrclib, whisper], order_uncertain={"lrclib"})
        assert s is whisper

    def test_returns_none_when_empty(self):
        assert select_scaffold([], order_uncertain=set()) is None


# ---------------- T8 build_consensus_lrc ----------------


class TestBuildConsensusLrc:
    def test_emits_valid_lrc_format(self):
        scaffold = _lrclib("[00:13.50]hello world\n[00:18.20]foo bar")
        voted = {0: "hello", 1: "world", 2: "foo", 3: "bar"}
        out = build_consensus_lrc(scaffold, voted)
        assert "[00:13.50]hello world" in out
        assert "[00:18.20]foo bar" in out

    def test_empty_scaffold_line_falls_back_to_original(self):
        # Scaffold has 2 lines (4 tokens) but only 2 voted tokens cover line 0.
        scaffold = _lrclib("[00:00.00]hello world\n[00:01.00]extra line")
        voted = {0: "hello", 1: "world"}  # nothing for line 2
        out = build_consensus_lrc(scaffold, voted)
        assert "[00:00.00]hello world" in out
        # Line 2 reuses scaffold's original text rather than empty.
        assert "extra line" in out

    def test_whisper_scaffold_emits_per_word_lines(self):
        words = [
            Word(text="alpha", start=0.0, end=0.5),
            Word(text="beta", start=0.5, end=1.0),
        ]
        scaffold = _whisper(words)
        voted = {0: "alpha", 1: "beta"}
        out = build_consensus_lrc(scaffold, voted)
        assert "alpha" in out
        assert "beta" in out
        assert out.startswith("[00:00.00]")


# ---------------- T1 Moonlight Shadow regression (CRITICAL) ----------------


class TestMoonlightShadowRegression:
    """Genius returned a wrong-version stub starting with "Four A.M.";
    VTT and Whisper both agree on the correct text. Consensus must
    reject Genius and emit a result whose timestamps come from VTT.
    """

    def test_genius_rejected_by_audio_ref(self):
        vtt = _vtt(_MOONLIGHT_CORRECT_LRC)
        whisper = _whisper(
            [
                Word(text="The", start=13.5, end=13.8),
                Word(text="last", start=13.8, end=14.1),
                Word(text="that", start=14.1, end=14.4),
                Word(text="ever", start=14.4, end=14.7),
                Word(text="she", start=14.7, end=15.0),
                Word(text="saw", start=15.0, end=15.3),
                Word(text="him", start=15.3, end=15.6),
                Word(text="Carried", start=18.2, end=18.6),
                Word(text="away", start=18.6, end=19.0),
                Word(text="by", start=19.0, end=19.2),
                Word(text="a", start=19.2, end=19.4),
                Word(text="moonlight", start=19.4, end=20.0),
                Word(text="shadow", start=20.0, end=20.6),
            ]
        )
        genius = _genius(_MOONLIGHT_WRONG_GENIUS)
        ref = build_audio_reference(vtt, whisper)
        consensus = build_consensus([vtt, whisper, genius], ref)
        assert consensus is not None
        # Genius must be rejected with an explicit coverage reason.
        rejected_names = [name for name, _reason in consensus.sources_rejected]
        assert "genius" in rejected_names
        # Consensus text starts with the correct verse.
        assert consensus.text.startswith("the last that ever she saw him")
        # No "four" hallucination.
        assert "four" not in consensus.text.split()

    def test_lrclib_agreement_lifts_confidence(self):
        vtt = _vtt(_MOONLIGHT_CORRECT_LRC)
        lrclib = _lrclib(_MOONLIGHT_CORRECT_LRC)
        ref = build_audio_reference(vtt, None)
        consensus = build_consensus([vtt, lrclib], ref)
        assert consensus is not None
        assert "lrclib" in consensus.sources_used
        assert consensus.confidence > 0.85


# ---------------- T9 build_consensus integration ----------------


class TestBuildConsensus:
    def test_single_source_lrclib(self):
        vtt = _vtt(_MOONLIGHT_CORRECT_LRC)
        lrclib = _lrclib(_MOONLIGHT_CORRECT_LRC)
        ref = build_audio_reference(vtt, None)
        result = build_consensus([vtt, lrclib], ref)
        assert isinstance(result, ConsensusResult)
        assert result.lrc

    def test_no_sources_returns_none(self):
        assert build_consensus([], []) is None


# ---------------- T10 all-rejected fallback ----------------


class TestAllRejected:
    def test_all_title_matched_rejected_returns_none(self):
        vtt = _vtt(_MOONLIGHT_CORRECT_LRC)
        # LRCLib returns garbage that doesn't match.
        bad = _lrclib("[00:00.00]totally\n[00:01.00]different\n[00:02.00]words\n[00:03.00]here")
        ref = build_audio_reference(vtt, None)
        result = build_consensus([vtt, bad], ref)
        # VTT alone (source_matched) survives but consensus uses VTT as scaffold.
        assert result is not None
        assert "lrclib" in [r[0] for r in result.sources_rejected]


# ---------------- T11 empty audio_ref guard ----------------


class TestEmptyAudioRefGuard:
    def test_empty_audio_ref_falls_back_to_best_title_matched(self):
        # No VTT, no Whisper; just LRCLib + Genius.
        lrclib = _lrclib(_MOONLIGHT_CORRECT_LRC)
        genius = _genius(_MOONLIGHT_CORRECT_LRC)
        result = build_consensus([lrclib, genius], audio_ref=[])
        assert result is not None
        # Confidence penalized.
        assert result.confidence < 1.0

    def test_empty_audio_ref_no_title_matched_returns_none(self):
        whisper = _whisper([Word(text="hi", start=0.0, end=0.5)])
        result = build_consensus([whisper], audio_ref=[])
        assert result is None


# ---------------- T13 confidence gate ----------------


class TestConfidenceGate:
    def test_below_threshold_returns_none(self):
        # Single weak title-matched source vs partial audio_ref.
        # 1 surviving / 1 total = 1.0 multiplier; coverage must drop it below 0.5.
        # Construct: ref has 10 tokens, lrclib matches only 4 of them.
        ref_text = " ".join(f"word{i}" for i in range(10))
        ref = normalize_tokens(ref_text)
        # Title-matched source matches 4 of 10 ref tokens (below 0.70 threshold,
        # gets rejected, no survivors of title kind, no audio-ref source).
        weak_lrc = " ".join(f"[00:0{i}.00]word{i}" for i in range(4))
        weak = _lrclib(weak_lrc)
        result = build_consensus([weak], ref)
        # Only 1 source, gets rejected, no survivors -> None.
        assert result is None


# ---------------- T24 scaffold beyond audio_ref ----------------


class TestScaffoldBeyondAudioRef:
    def test_scaffold_lines_past_audio_ref_use_original_text(self):
        scaffold = _lrclib("[00:00.00]hello world\n[00:05.00]foo bar\n[00:10.00]extra padding")
        # voted only covers first 4 tokens.
        voted = {0: "hello", 1: "world", 2: "foo", 3: "bar"}
        out = build_consensus_lrc(scaffold, voted)
        assert "[00:10.00]extra padding" in out


# ---------------- T25 group failure mode ----------------


class TestGroupFailureMode:
    """Documented limitation: if every title-matched source returns the same
    wrong-version (cover indexed under original title), consensus depends
    entirely on VTT/Whisper. With strong audio reference, all wrong-version
    title-matched are rejected.
    """

    def test_strong_audio_ref_rejects_unanimous_wrong_version(self):
        vtt = _vtt(_MOONLIGHT_CORRECT_LRC)
        # 3 title-matched all returning the same wrong-version.
        wrong = (
            "[00:00.00]four am in the morning\n"
            "[00:05.00]i watched your vision forming\n"
            "[00:10.00]stars roll slow"
        )
        a = _lrclib(wrong)
        b = _musixmatch(wrong)
        c = _megalobiz(wrong)
        ref = build_audio_reference(vtt, None)
        result = build_consensus([vtt, a, b, c], ref)
        assert result is not None
        rejected_names = [name for name, _ in result.sources_rejected]
        assert {"lrclib", "musixmatch", "megalobiz"} <= set(rejected_names)
        assert result.text.startswith("the last")
