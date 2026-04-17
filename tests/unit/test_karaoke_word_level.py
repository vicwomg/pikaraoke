"""Tests for the word-level-lyrics alignment status helper and startup banner."""

from unittest.mock import patch

import pytest

from pikaraoke.karaoke import (
    _build_lyrics_aligner,
    _warn_word_level_disabled,
    word_level_lyrics_status,
)


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("WHISPERX_MODEL", raising=False)
    monkeypatch.delenv("WHISPERX_DEVICE", raising=False)


class TestWordLevelLyricsStatus:
    def test_whisperx_missing(self, no_env):
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=False):
            status = word_level_lyrics_status()
        assert status["enabled"] is False
        assert status["explicit_opt_out"] is False
        assert "not installed" in status["reason"]
        assert "pip install" in status["fix"]

    def test_env_unset_but_whisperx_installed(self, no_env):
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=True):
            status = word_level_lyrics_status()
        assert status["enabled"] is False
        assert status["explicit_opt_out"] is False
        assert "WHISPERX_MODEL" in status["reason"]
        assert "export WHISPERX_MODEL=" in status["fix"]

    @pytest.mark.parametrize("value", ["off", "OFF", "none", "false", "0"])
    def test_explicit_opt_out(self, monkeypatch, value):
        monkeypatch.setenv("WHISPERX_MODEL", value)
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=False):
            status = word_level_lyrics_status()
        assert status["enabled"] is False
        assert status["explicit_opt_out"] is True
        assert status["fix"] is None

    def test_enabled(self, monkeypatch):
        monkeypatch.setenv("WHISPERX_MODEL", "base")
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=True):
            status = word_level_lyrics_status()
        assert status["enabled"] is True
        assert status["model"] == "base"
        assert status["reason"] is None
        assert status["explicit_opt_out"] is False

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("WHISPERX_MODEL", "  base  ")
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=True):
            status = word_level_lyrics_status()
        assert status["enabled"] is True
        assert status["model"] == "base"


class TestBuildLyricsAligner:
    def test_warns_and_returns_none_when_whisperx_missing(self, no_env):
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=False), patch(
            "pikaraoke.karaoke._warn_word_level_disabled"
        ) as mock_warn:
            aligner = _build_lyrics_aligner()
        assert aligner is None
        mock_warn.assert_called_once()
        kwargs = mock_warn.call_args.kwargs
        assert "not installed" in kwargs["reason"]
        assert "pip install" in kwargs["fix"]

    def test_warns_and_returns_none_when_model_unset(self, no_env):
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=True), patch(
            "pikaraoke.karaoke._warn_word_level_disabled"
        ) as mock_warn:
            aligner = _build_lyrics_aligner()
        assert aligner is None
        mock_warn.assert_called_once()

    def test_silent_when_explicitly_opted_out(self, monkeypatch):
        monkeypatch.setenv("WHISPERX_MODEL", "off")
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=False), patch(
            "pikaraoke.karaoke._warn_word_level_disabled"
        ) as mock_warn:
            aligner = _build_lyrics_aligner()
        assert aligner is None
        mock_warn.assert_not_called()

    def test_returns_aligner_when_enabled(self, monkeypatch):
        monkeypatch.setenv("WHISPERX_MODEL", "base")
        monkeypatch.setenv("WHISPERX_DEVICE", "cpu")
        fake_aligner = object()
        fake_cls = patch(
            "pikaraoke.lib.lyrics_align.WhisperXAligner",
            return_value=fake_aligner,
        )
        with patch("pikaraoke.karaoke._is_whisperx_installed", return_value=True), fake_cls as mock:
            aligner = _build_lyrics_aligner()
        assert aligner is fake_aligner
        mock.assert_called_once_with(model_size="base", device="cpu")


class TestWarnBanner:
    def test_logs_at_warning_level_and_contains_fix(self, caplog):
        with caplog.at_level("WARNING"):
            _warn_word_level_disabled(reason="test reason", fix="do thing")
        messages = "\n".join(r.message for r in caplog.records)
        assert "WARNING" in messages
        assert "test reason" in messages
        assert "do thing" in messages
        assert "WHISPERX_MODEL=off" in messages  # silence hint
