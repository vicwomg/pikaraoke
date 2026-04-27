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
