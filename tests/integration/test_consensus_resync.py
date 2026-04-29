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
    db.update_track_metadata(sid, artist="Bonnie Tyler", title="Total Eclipse", duration_seconds=210.0)
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


def _common_patches(audio_duration_s: float):
    """Quiet down the orchestrator's external calls so the test focuses
    on the grader → line-template branch."""
    return [
        patch("pikaraoke.lib.lyrics._wait_for_alignment_audio", side_effect=lambda p: p),
        patch("pikaraoke.lib.lyrics._estimate_bpm", return_value=120.0),
        patch("pikaraoke.lib.lyrics_align._probe_audio_duration", return_value=audio_duration_s),
        patch("pikaraoke.lib.lyrics._whisper_fallback_enabled", return_value=False),
        patch("pikaraoke.lib.lyrics.GENIUS_ACCESS_TOKEN", ""),
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
    service = LyricsService(os.path.dirname(song), EventSystem(), aligner=aligner, db=db)

    _stub_consensus(monkeypatch, upstream_lrc, plain_text)

    with _common_patches(audio_duration_s=60.0)[0], _common_patches(60.0)[1], _common_patches(
        60.0
    )[2], _common_patches(60.0)[3], _common_patches(60.0)[4]:
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
    service = LyricsService(os.path.dirname(song), EventSystem(), aligner=aligner, db=db)

    _stub_consensus(monkeypatch, upstream_lrc, plain_text)

    with _common_patches(audio_duration_s=60.0)[0], _common_patches(60.0)[1], _common_patches(
        60.0
    )[2], _common_patches(60.0)[3], _common_patches(60.0)[4]:
        service._upgrade_via_consensus(song, {"track": "x", "artist": "y"}, upstream_lrc, "sha")

    ass_path = song.replace(".mp4", ".ass")
    assert os.path.exists(ass_path), "T3 ASS not written"
    ass_text = open(ass_path, encoding="utf-8").read()
    starts = [_ts_to_seconds(m) for m in ASS_DIALOGUE_RE.findall(ass_text)]
    assert starts, "no Dialogue lines in ASS"

    confidence = db.get_song_by_id(sid)["lyrics_confidence"]
    assert confidence is not None
    assert confidence >= 0.75
