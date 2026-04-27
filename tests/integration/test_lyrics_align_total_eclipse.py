"""End-to-end alignment regression for Bonnie Tyler - Total Eclipse of the Heart.

Pins behaviour against captured silero+silencedetect onsets and the
LRClib syncedLyrics for the song. The bug this guards: ghost LRC lines
highlighted during the 2:50-3:28 instrumental and a 2 s lateness
cascade through the post-solo verse until 4:17. The DP solver in
``lyrics_align`` must interpolate the unanchored lines into the audio
window after the post-solo onset, not into the dead solo.
"""

import json
from pathlib import Path

import pytest

from pikaraoke.lib import lyrics_align, vad_probe
from pikaraoke.lib.lyrics import lrc_line_windows

FIXTURES = Path(__file__).parent.parent / "fixtures" / "total_eclipse"
SOLO_START_S = 170.0  # last pre-solo phrase decays around 2:50
SOLO_END_S = 207.0  # post-solo first audio onset is 207.84
EXPECTED_FIRST_POST_SOLO_S = 207.84
LEAD_IN_S = 0.25  # _KARAOKE_LEAD_IN_S


@pytest.fixture
def total_eclipse_inputs(monkeypatch):
    onsets = json.loads((FIXTURES / "vocal_onsets.json").read_text())["onsets"]
    lrc = (FIXTURES / "lyrics.lrc").read_text()
    monkeypatch.setattr(
        vad_probe,
        "list_vocal_onsets",
        lambda _path: [(e["onset"], e["next_onset"]) for e in onsets],
    )
    return lrc_line_windows(lrc)


def test_no_line_renders_during_solo(total_eclipse_inputs):
    """No LRC line's shifted start time may fall inside 2:50-3:28."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    assert out is not None, "alignment should not bail on Total Eclipse"

    in_solo = [
        (i, t, total_eclipse_inputs[i][2])
        for i, t in enumerate(out)
        if SOLO_START_S < t < SOLO_END_S and total_eclipse_inputs[i][2].strip()
    ]
    assert not in_solo, (
        f"{len(in_solo)} ghost line(s) render during the instrumental "
        f"solo (170s-207s). First three: {in_solo[:3]}"
    )


def test_first_post_solo_line_snaps_to_real_onset(total_eclipse_inputs):
    """The first line at or after 3:28 starts within 2 s of the real onset."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    assert out is not None
    post_solo = [t for t in out if t >= SOLO_END_S]
    first_post = min(post_solo)
    expected = EXPECTED_FIRST_POST_SOLO_S - LEAD_IN_S
    assert abs(first_post - expected) < 2.0, (
        f"first post-solo line at {first_post:.2f}s, "
        f"expected within 2s of {expected:.2f}s"
    )


def test_lateness_recovers_by_417(total_eclipse_inputs):
    """Lines whose shifted start is in 207.84..257.56 must spread out, not
    pile up against the earlier anchor (the "compressed at one anchor"
    cascade we're fixing)."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    assert out is not None
    in_window = sorted(t for t in out if 207.84 <= t <= 257.56)
    if len(in_window) < 2:
        pytest.skip("not enough lines in the post-solo window for this assertion")
    consecutive_gaps = [b - a for a, b in zip(in_window, in_window[1:])]
    assert min(consecutive_gaps) >= 0.05, (
        f"post-solo lines are compressed: min gap = {min(consecutive_gaps):.3f}s"
    )


def test_full_pipeline_smoke(total_eclipse_inputs):
    """Sanity: every line gets a shifted timestamp, all in [0, audio + slack]."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    assert out is not None
    assert len(out) == len(total_eclipse_inputs)
    assert all(0 <= t <= 340 for t in out)
    inversions = sum(1 for a, b in zip(out, out[1:]) if b + 0.5 < a)
    assert inversions == 0, (
        f"{inversions} large monotonicity inversions in shifted starts"
    )
