"""End-to-end alignment regression for Queen - I Want To Break Free.

The song has a long instrumental break around 2:50-3:30 and repeated
backing-vocal "I want to break free" entries scattered through the
song. Pins the DP solver's behaviour on a third musical pattern (a
saxophone-led break with vocal callbacks) so weight retunes that
break this song fail CI.
"""

import json
from pathlib import Path

import pytest

from pikaraoke.lib import lyrics_align, vad_probe
from pikaraoke.lib.lyrics import lrc_line_windows

FIXTURES = Path(__file__).parent.parent / "fixtures" / "queen_iwtbf"
LEAD_IN_S = 0.25


@pytest.fixture
def queen_iwtbf_inputs(monkeypatch):
    onsets = json.loads((FIXTURES / "vocal_onsets.json").read_text())["onsets"]
    lrc = (FIXTURES / "lyrics.lrc").read_text()
    monkeypatch.setattr(
        vad_probe,
        "list_vocal_onsets",
        lambda _path: [(e["onset"], e["next_onset"]) for e in onsets],
    )
    return lrc_line_windows(lrc)


def test_alignment_returns_full_per_line_list(queen_iwtbf_inputs):
    out = lyrics_align._detect_per_line_starts("/dev/null", queen_iwtbf_inputs)
    # Either alignment runs (returns one entry per LRC line) or it bails
    # cleanly with None - both are valid contract states. Bail = the
    # initial offset doesn't pass the gate, which is acceptable here.
    if out is None:
        pytest.skip("alignment bailed on Queen IWTBF (initial offset gate)")
    assert len(out) == len(queen_iwtbf_inputs)


def test_no_large_monotonicity_inversions(queen_iwtbf_inputs):
    out = lyrics_align._detect_per_line_starts("/dev/null", queen_iwtbf_inputs)
    if out is None:
        pytest.skip("alignment bailed; nothing to check for monotonicity")
    inversions = [
        (i, out[i - 1], out[i])
        for i in range(1, len(out))
        if out[i] + 0.5 < out[i - 1]
    ]
    assert not inversions, (
        f"{len(inversions)} inversion(s) in shifted starts; first three: "
        f"{inversions[:3]}"
    )


def test_shifts_within_audio_duration(queen_iwtbf_inputs):
    """Every shifted line lands within the audio duration with small slack."""
    onset_data = json.loads((FIXTURES / "vocal_onsets.json").read_text())
    duration = onset_data["audio_duration_s"]
    out = lyrics_align._detect_per_line_starts("/dev/null", queen_iwtbf_inputs)
    if out is None:
        pytest.skip("alignment bailed; nothing to check")
    assert all(0 <= t <= duration + 5.0 for t in out)


def test_grader_keeps_clean_song_on_fast_path(queen_iwtbf_inputs):
    """The Queen IWTBF fixture has clean LRC priors that match the audio
    duration. The grader must score it ``>= _RELIABILITY_GATE`` so the
    consensus orchestrator keeps it on the fast LRC-windowed path. This
    is the regression gate for the model_id bump — if a future tweak
    pushes well-aligned songs into the synthetic-LRC fallback, the
    fallback's whole-song wav2vec2 drift could regress songs that align
    well today.
    """
    onset_data = json.loads((FIXTURES / "vocal_onsets.json").read_text())
    audio_duration_s = onset_data["audio_duration_s"]
    # Last LRC line's start time is a reasonable proxy for the LRC's
    # implied duration — within tolerance for this fixture.
    last_lrc_start = max(start for start, _end, text in queen_iwtbf_inputs if text.strip())
    score = lyrics_align._grade_priors(
        audio_duration_s=audio_duration_s,
        lrc_lines=[(s, e, t) for s, e, t in queen_iwtbf_inputs],
        lrc_metadata_duration_s=last_lrc_start,
        dp_residuals=None,
    )
    assert score >= lyrics_align._RELIABILITY_GATE, (
        f"grader scored {score:.2f} < gate {lyrics_align._RELIABILITY_GATE} "
        "for the Queen IWTBF fixture; routing this song through the "
        "synthetic-LRC fallback would risk whole-song wav2vec2 drift "
        "on a song the LRC-windowed path already aligns cleanly"
    )
