"""Unit tests for module-level Karaoke helpers (sweep + VAD prewarm).

Full ``Karaoke`` construction touches DB, scanner, and many other
subsystems; the sweep logic is exercised here by calling the helper
function ``_ass_stale_model_id`` directly and by running
``Karaoke._sweep_stale_aligned_ass`` against a hand-rolled stub object
that exposes only the attributes the method reads.
"""

from types import SimpleNamespace

from pikaraoke.karaoke import Karaoke, _ass_stale_model_id


class TestAssStaleModelId:
    def _write_ass(self, path, marker_line: str | None) -> None:
        marker = f"; model_id: {marker_line}\n" if marker_line else ""
        path.write_text(
            "[Script Info]\n"
            "Title: PiKaraoke Auto-Lyrics\n"
            f"{marker}"
            "ScriptType: v4.00+\n",
            encoding="utf-8",
        )

    def test_returns_none_when_marker_matches(self, tmp_path):
        ass = tmp_path / "song.ass"
        self._write_ass(ass, "wav2vec2-char-vad-dpalign")
        assert _ass_stale_model_id(str(ass), "wav2vec2-char-vad-dpalign") is None

    def test_returns_old_id_when_marker_is_stale(self, tmp_path):
        ass = tmp_path / "song.ass"
        self._write_ass(ass, "wav2vec2-char-perline")
        assert _ass_stale_model_id(str(ass), "wav2vec2-char-vad-dpalign") == (
            "wav2vec2-char-perline"
        )

    def test_returns_none_when_no_marker(self, tmp_path):
        # User-authored .ass files have no model_id comment - skip them.
        ass = tmp_path / "song.ass"
        self._write_ass(ass, None)
        assert _ass_stale_model_id(str(ass), "wav2vec2-char-vad-dpalign") is None

    def test_returns_none_when_file_unreadable(self, tmp_path):
        # Conservative: if we can't read the file, never delete it.
        assert _ass_stale_model_id(str(tmp_path / "missing.ass"), "any") is None


class TestStartupSweepsStaleAlignedAss:
    def test_startup_sweeps_stale_aligned_ass(self, tmp_path):
        # Two .ass files in the songs dir: one current, one stale.
        # Sweep removes only the stale one and leaves the current intact.
        current = "wav2vec2-char-vad-dpalign"
        stale_ass = tmp_path / "stale.ass"
        stale_ass.write_text(
            "[Script Info]\n"
            "Title: PiKaraoke Auto-Lyrics\n"
            "; model_id: wav2vec2-char-perline\n"
            "ScriptType: v4.00+\n",
            encoding="utf-8",
        )
        current_ass = tmp_path / "current.ass"
        current_ass.write_text(
            "[Script Info]\n"
            "Title: PiKaraoke Auto-Lyrics\n"
            f"; model_id: {current}\n"
            "ScriptType: v4.00+\n",
            encoding="utf-8",
        )
        user_ass = tmp_path / "user.ass"
        # User-authored .ass without the marker stays untouched.
        user_ass.write_text(
            "[Script Info]\nTitle: My Custom Lyrics\nScriptType: v4.00+\n",
            encoding="utf-8",
        )

        # Stub Karaoke with only the attributes the sweep reads.
        stub = SimpleNamespace(
            _aligner_instance=SimpleNamespace(model_id=current),
            download_path=str(tmp_path),
        )
        Karaoke._sweep_stale_aligned_ass(stub)

        assert not stale_ass.exists(), "stale .ass should be unlinked"
        assert current_ass.exists(), "current .ass must survive"
        assert user_ass.exists(), "user-authored .ass must survive"

    def test_sweep_no_op_when_aligner_disabled(self, tmp_path):
        # No aligner -> the model_id check has no current value, sweep
        # must not delete anything.
        ass = tmp_path / "any.ass"
        ass.write_text(
            "[Script Info]\nTitle: PiKaraoke Auto-Lyrics\n"
            "; model_id: wav2vec2-char-perline\nScriptType: v4.00+\n",
            encoding="utf-8",
        )
        stub = SimpleNamespace(_aligner_instance=None, download_path=str(tmp_path))
        Karaoke._sweep_stale_aligned_ass(stub)
        assert ass.exists()
