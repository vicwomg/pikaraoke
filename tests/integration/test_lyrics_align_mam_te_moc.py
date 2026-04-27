"""End-to-end alignment regression for Katarzyna Łaska - Mam tę moc.

Pins per-verse drift behaviour: the YouTube version of this Polish
ballad slows down across verses, so a single global shift would
over-correct line 1 or under-correct line 6. The DP solver must anchor
each verse's first line to its own onset and only inherit cumulative
shifts within continuous singing inside a verse.
"""

import json
from pathlib import Path

import pytest

from pikaraoke.lib import lyrics_align, vad_probe
from pikaraoke.lib.lyrics import lrc_line_windows

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mam_te_moc"
LEAD_IN_S = 0.25


@pytest.fixture
def mam_te_moc_inputs(monkeypatch):
    onsets = json.loads((FIXTURES / "vocal_onsets.json").read_text())["onsets"]
    lrc = (FIXTURES / "lyrics.lrc").read_text()
    monkeypatch.setattr(
        vad_probe,
        "list_vocal_onsets",
        lambda _path: [(e["onset"], e["next_onset"]) for e in onsets],
    )
    return lrc_line_windows(lrc)


def test_first_line_snaps_to_first_onset(mam_te_moc_inputs):
    """The first non-empty LRC line snaps near the first VAD onset."""
    out = lyrics_align._detect_per_line_starts("/dev/null", mam_te_moc_inputs)
    assert out is not None
    first_text_idx = next(
        i for i, (_, _, t) in enumerate(mam_te_moc_inputs) if t.strip()
    )
    onsets = json.loads((FIXTURES / "vocal_onsets.json").read_text())["onsets"]
    first_onset = onsets[0]["onset"]
    # The first non-empty LRC line should land within ~2 s of the first
    # onset (allowing for the karaoke lead-in subtraction).
    assert abs(out[first_text_idx] - (first_onset - LEAD_IN_S)) < 2.0


def test_per_verse_shifts_grow_monotonically(mam_te_moc_inputs):
    """Cumulative shift (shifted - original) grows from verse to verse,
    not flatly applied as a single global offset."""
    out = lyrics_align._detect_per_line_starts("/dev/null", mam_te_moc_inputs)
    assert out is not None
    shifts = [
        (i, out[i] - mam_te_moc_inputs[i][0])
        for i, (_, _, t) in enumerate(mam_te_moc_inputs)
        if t.strip()
    ]
    # Drift should not be perfectly flat - a global shift would have
    # zero variance across the song.
    shift_values = [s for _, s in shifts]
    span = max(shift_values) - min(shift_values)
    assert span >= 0.5, (
        f"per-verse drift collapsed to a global shift (span={span:.3f}s); "
        "DP must anchor multiple verses, not just the first line"
    )


def test_no_large_monotonicity_inversions(mam_te_moc_inputs):
    """Shifted times must not jump backward by more than 0.5 s between lines.
    A backward jump is a wrong-anchor signal; the DP's tempo-jump cost
    should keep the assignment monotonic across verses."""
    out = lyrics_align._detect_per_line_starts("/dev/null", mam_te_moc_inputs)
    assert out is not None
    inversions = [
        (i, out[i - 1], out[i])
        for i in range(1, len(out))
        if out[i] + 0.5 < out[i - 1]
    ]
    assert not inversions, (
        f"{len(inversions)} inversion(s) in shifted starts; first three: "
        f"{inversions[:3]}"
    )
