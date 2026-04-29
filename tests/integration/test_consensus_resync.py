"""Synthetic-LRC fallback regression for the consensus orchestrator.

When the prior-reliability grader scores a song below ``_RELIABILITY_GATE``
the rendered ASS must use a line template rebuilt from the aligned words
(``_lrc_from_aligned_lines``) instead of the upstream consensus LRC. The
classic failure mode this guards: LRCLib returns correct text but
off-by-one timestamps for a different edit; per-word timings come out
right but the line fence is still off because it inherited the upstream
timestamps.

The test stubs the aligner, the audio probe, and the consensus build so
the orchestrator runs end-to-end against an in-memory DB and writes a
T3 ASS file. It then asserts the Dialogue start times match the
synthetic LRC's tags, not the consensus LRC's tags.
"""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib import lyrics_consensus as lc
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.lyrics import LyricsService, Word

ASS_DIALOGUE_RE = re.compile(r"^Dialogue:\s*\d+\s*,\s*([0-9:.]+)\s*,", re.MULTILINE)


def _ts_to_seconds(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@pytest.fixture
def consensus_song(tmp_path):
    db = KaraokeDatabase(str(tmp_path / "test.db"))
    song = str(tmp_path / "Foo---abc.mp4")
    open(song, "w").close()
    db.insert_songs([{"file_path": song, "youtube_id": "abc", "format": "mp4"}])
    sid = db.get_song_id_by_path(song)
    db.update_track_metadata(
        sid, artist="Bonnie Tyler", title="Total Eclipse", duration_seconds=210.0
    )
    return song, db, sid


def _stub_consensus(monkeypatch, lrc_text: str, plain_text: str):
    """Force ``build_consensus`` to return a fixed result so the test
    drives only the post-consensus branch (grader → line template)."""

    def fake_build_consensus(sources, audio_ref):
        return lc.ConsensusResult(
            text=plain_text,
            lrc=lrc_text,
            sources_used=["lrclib"],
            sources_rejected=[],
            confidence=0.95,
        )

    monkeypatch.setattr("pikaraoke.lib.lyrics_consensus.build_consensus", fake_build_consensus)


def _common_patches(audio_duration_s: float, onsets: list[tuple[float, float]] | None = None):
    """Quiet down the orchestrator's external calls so the test focuses
    on the grader → line-template branch.

    ``onsets`` controls what ``grade_lrc_priors_against_audio`` sees for
    the DP-residuals signal. None → empty (DP doesn't run, grader uses
    duration alone). A list of ``(onset, next_onset)`` pairs lets a test
    drive the DP into a clean / shifted / rejected residual.
    """
    onsets = onsets if onsets is not None else []
    return [
        patch("pikaraoke.lib.lyrics._wait_for_alignment_audio", side_effect=lambda p: p),
        patch("pikaraoke.lib.lyrics._estimate_bpm", return_value=120.0),
        patch("pikaraoke.lib.lyrics_align._probe_audio_duration", return_value=audio_duration_s),
        patch("pikaraoke.lib.lyrics._whisper_fallback_enabled", return_value=False),
        patch("pikaraoke.lib.lyrics.GENIUS_ACCESS_TOKEN", ""),
        patch(
            "pikaraoke.lib.lyrics_align.vad_probe.list_vocal_onsets",
            return_value=list(onsets),
        ),
    ]


def test_low_confidence_uses_synthetic_lrc(consensus_song, monkeypatch):
    """LRC duration far past audio → grader < gate → synthetic LRC wins."""
    song, db, sid = consensus_song

    # Upstream LRC tags are 60s ahead of audio (long-version timestamps).
    upstream_lrc = "[02:00.00]hello world\n[02:05.00]again again"
    plain_text = "hello world\nagain again"
    aligned_words = [
        Word("hello", 1.0, 1.4),
        Word("world", 1.4, 1.9),
        Word("again", 6.0, 6.4),
        Word("again", 6.4, 6.9),
    ]

    aligner = MagicMock()
    aligner.align.return_value = aligned_words
    aligner.model_name = "wav2vec2-test"
    aligner.last_line_starts = {}
    service = LyricsService(os.path.dirname(song), EventSystem(), aligner=aligner, db=db)

    _stub_consensus(monkeypatch, upstream_lrc, plain_text)

    patches = _common_patches(audio_duration_s=60.0)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        service._upgrade_via_consensus(song, {"track": "x", "artist": "y"}, upstream_lrc, "sha")

    ass_path = song.replace(".mp4", ".ass")
    assert os.path.exists(ass_path), "T3 ASS not written"
    ass_text = open(ass_path, encoding="utf-8").read()
    starts = [_ts_to_seconds(m) for m in ASS_DIALOGUE_RE.findall(ass_text)]
    assert starts, "no Dialogue lines in ASS"

    # Synthetic LRC timestamps come from the aligned words: line 1 starts
    # near 1.0s, line 2 near 6.0s. The upstream consensus LRC would have
    # placed them at 120s and 125s. So the first Dialogue must land near
    # 1.0s, never near 120s.
    assert starts[0] < 5.0, f"first Dialogue at {starts[0]:.2f}s, expected <5s (synthetic)"
    assert all(t < 60.0 for t in starts), (
        f"Dialogue starts {starts} include upstream-LRC timestamps; "
        "synthetic line template did not take effect"
    )

    confidence = db.get_song_by_id(sid)["lyrics_confidence"]
    assert confidence is not None
    assert confidence < 0.75
    # Low-confidence routing skips the line-windowed alignment (no
    # ``lrc_lines`` argument); whole-song alignment is the safer fallback
    # when LRC priors look unreliable.
    align_kwargs = aligner.align.call_args.kwargs
    assert "lrc_lines" not in align_kwargs, (
        f"low-confidence path passed lrc_lines={align_kwargs.get('lrc_lines')!r}; "
        "should have routed to whole-song alignment"
    )


def test_high_confidence_keeps_consensus_lrc(consensus_song, monkeypatch):
    """Matching durations → grader >= gate → upstream LRC drives the fence."""
    song, db, sid = consensus_song

    # Upstream LRC matches the audio's ~60s duration: last tag at 55s.
    upstream_lrc = "[00:01.00]hello world\n[00:55.00]again again"
    plain_text = "hello world\nagain again"
    aligned_words = [
        Word("hello", 1.0, 1.4),
        Word("world", 1.4, 1.9),
        Word("again", 55.0, 55.4),
        Word("again", 55.4, 55.9),
    ]

    aligner = MagicMock()
    aligner.align.return_value = aligned_words
    aligner.model_name = "wav2vec2-test"
    aligner.last_line_starts = {}
    service = LyricsService(os.path.dirname(song), EventSystem(), aligner=aligner, db=db)

    _stub_consensus(monkeypatch, upstream_lrc, plain_text)

    # VAD onsets that match the upstream LRC timestamps cleanly so the
    # DP anchors both lines with no rejections and a small max shift —
    # the grader's residual factors stay near 1.0 and combined with the
    # matched audio duration the score lands well above the gate.
    clean_onsets = [(1.5, 55.0), (55.5, 60.0)]
    patches = _common_patches(audio_duration_s=60.0, onsets=clean_onsets)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        service._upgrade_via_consensus(song, {"track": "x", "artist": "y"}, upstream_lrc, "sha")

    ass_path = song.replace(".mp4", ".ass")
    assert os.path.exists(ass_path), "T3 ASS not written"
    ass_text = open(ass_path, encoding="utf-8").read()
    starts = [_ts_to_seconds(m) for m in ASS_DIALOGUE_RE.findall(ass_text)]
    assert starts, "no Dialogue lines in ASS"

    confidence = db.get_song_by_id(sid)["lyrics_confidence"]
    assert confidence is not None
    assert confidence >= 0.75
    # High-confidence routing now drives the aligner with the consensus
    # LRC's line windows — the orchestrator must propagate ``lrc_lines``
    # so wav2vec2 segments cover real audio per line, not the whole song.
    align_kwargs = aligner.align.call_args.kwargs
    assert align_kwargs.get("lrc_lines"), (
        "high-confidence path did not pass lrc_lines to the aligner; "
        "orchestrator regressed to whole-song alignment"
    )


def test_replay_skips_regrade_when_persisted_score_above_gate(consensus_song, monkeypatch):
    """A second consensus run reads the persisted score and short-circuits
    the audio probe + DP — the orchestrator must not re-call vad_probe
    when the previous run already produced a passing score.
    """
    song, db, sid = consensus_song

    # Pre-seed the column with a passing score, mimicking a prior run.
    db.update_lyrics_confidence(sid, 0.92)

    upstream_lrc = "[00:01.00]hello world\n[00:55.00]again again"
    plain_text = "hello world\nagain again"
    aligned_words = [
        Word("hello", 1.0, 1.4),
        Word("world", 1.4, 1.9),
        Word("again", 55.0, 55.4),
        Word("again", 55.4, 55.9),
    ]

    aligner = MagicMock()
    aligner.align.return_value = aligned_words
    aligner.model_name = "wav2vec2-test"
    aligner.last_line_starts = {}
    service = LyricsService(os.path.dirname(song), EventSystem(), aligner=aligner, db=db)

    _stub_consensus(monkeypatch, upstream_lrc, plain_text)

    # No onsets supplied: if replay short-circuits as expected the DP
    # never runs, so vad_probe is never consulted. If replay is skipped
    # the empty onsets list would still produce a finite (low) score and
    # the assertion below would catch the regression.
    patches = _common_patches(audio_duration_s=60.0, onsets=[])
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as vad_mock:
        service._upgrade_via_consensus(song, {"track": "x", "artist": "y"}, upstream_lrc, "sha")

    confidence = db.get_song_by_id(sid)["lyrics_confidence"]
    assert confidence == pytest.approx(
        0.92
    ), f"replay overwrote persisted confidence {confidence!r}; expected 0.92"
    assert vad_mock.call_count == 0, (
        "replay invoked vad_probe; the persisted score should have "
        "short-circuited the pre-grade probe"
    )
    align_kwargs = aligner.align.call_args.kwargs
    assert align_kwargs.get("lrc_lines"), (
        "replay-on-passing-score did not preserve the high-confidence "
        "line-windowed alignment routing"
    )
