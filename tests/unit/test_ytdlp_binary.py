"""Unit tests for the ytdlp_binary module. Network I/O is mocked."""

import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib import ytdlp_binary


def _set_platform(
    monkeypatch,
    *,
    windows=False,
    macos=False,
    linux=False,
    android=False,
    machine="x86_64",
    musl=False,
):
    monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: windows)
    monkeypatch.setattr(ytdlp_binary, "is_macos", lambda: macos)
    monkeypatch.setattr(ytdlp_binary, "is_linux", lambda: linux)
    monkeypatch.setattr(ytdlp_binary, "is_android", lambda: android)
    monkeypatch.setattr(ytdlp_binary, "_is_musl", lambda: musl)
    monkeypatch.setattr(ytdlp_binary.platform, "machine", lambda: machine)


class TestAssetName:
    """_asset_name maps platform + arch to the right release asset, or None."""

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            (dict(windows=True, machine="AMD64"), "yt-dlp.exe"),
            (dict(windows=True, machine="ARM64"), "yt-dlp_arm64.exe"),
            (dict(windows=True, machine="x86"), "yt-dlp_x86.exe"),
            (dict(macos=True, machine="arm64"), "yt-dlp_macos"),
            (dict(macos=True, machine="x86_64"), "yt-dlp_macos"),
            (dict(linux=True, machine="x86_64"), "yt-dlp_linux"),
            (dict(linux=True, machine="aarch64"), "yt-dlp_linux_aarch64"),
            (dict(linux=True, machine="x86_64", musl=True), "yt-dlp_musllinux"),
            (dict(linux=True, machine="aarch64", musl=True), "yt-dlp_musllinux_aarch64"),
        ],
    )
    def test_supported_platforms(self, monkeypatch, kwargs, expected):
        _set_platform(monkeypatch, **kwargs)
        assert ytdlp_binary._asset_name() == expected

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(linux=True, machine="armv7l"),  # 32-bit ARM: only a non-updating zip
            dict(linux=True, machine="armv6l"),
            dict(android=True, machine="aarch64"),  # bionic libc runs none of them
            dict(machine="x86_64"),  # unknown OS
        ],
    )
    def test_unsupported_platforms_fall_back(self, monkeypatch, kwargs):
        _set_platform(monkeypatch, **kwargs)
        assert ytdlp_binary._asset_name() is None


class TestBinaryPathAndCommand:
    """binary_path and ytdlp_command locate and prefer the managed binary."""

    def test_binary_path_extension(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "get_data_directory", lambda: str(tmp_path))
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: True)
        assert ytdlp_binary.binary_path() == os.path.join(str(tmp_path), "yt-dlp.exe")
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        assert ytdlp_binary.binary_path() == os.path.join(str(tmp_path), "yt-dlp")

    def test_command_prefers_binary_when_present(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "get_data_directory", lambda: str(tmp_path))
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        binary = tmp_path / "yt-dlp"
        binary.write_text("#!/bin/sh\n")
        assert ytdlp_binary.ytdlp_command() == [str(binary)]

    def test_command_falls_back_to_module(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "get_data_directory", lambda: str(tmp_path))
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        cmd = ytdlp_binary.ytdlp_command()
        assert cmd[-2:] == ["-m", "yt_dlp"]


class TestEnsureBinary:
    """ensure_ytdlp_binary short-circuits and never raises."""

    def test_no_asset_returns_none(self, monkeypatch):
        monkeypatch.setattr(ytdlp_binary, "_asset_name", lambda: None)
        assert ytdlp_binary.ensure_ytdlp_binary() is None

    def test_existing_binary_is_not_redownloaded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "_asset_name", lambda: "yt-dlp_linux")
        monkeypatch.setattr(ytdlp_binary, "get_data_directory", lambda: str(tmp_path))
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        (tmp_path / "yt-dlp").write_text("binary")
        download = MagicMock()
        monkeypatch.setattr(ytdlp_binary, "_download_and_verify", download)
        assert ytdlp_binary.ensure_ytdlp_binary() == os.path.join(str(tmp_path), "yt-dlp")
        download.assert_not_called()

    def test_missing_binary_triggers_download(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "_asset_name", lambda: "yt-dlp_linux")
        monkeypatch.setattr(ytdlp_binary, "get_data_directory", lambda: str(tmp_path))
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        monkeypatch.setattr(ytdlp_binary, "_download_and_verify", lambda asset, dest: True)
        assert ytdlp_binary.ensure_ytdlp_binary() == os.path.join(str(tmp_path), "yt-dlp")

    def test_failed_download_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "_asset_name", lambda: "yt-dlp_linux")
        monkeypatch.setattr(ytdlp_binary, "get_data_directory", lambda: str(tmp_path))
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        monkeypatch.setattr(ytdlp_binary, "_download_and_verify", lambda asset, dest: False)
        assert ytdlp_binary.ensure_ytdlp_binary() is None


class TestExpectedHash:
    """_expected_hash reads the GNU-format checksums file."""

    def test_matches_asset_line(self):
        text = "aaa  yt-dlp.exe\nbbb  yt-dlp_linux\n"
        assert ytdlp_binary._expected_hash(text, "yt-dlp_linux") == "bbb"

    def test_missing_asset_returns_none(self):
        assert ytdlp_binary._expected_hash("aaa  yt-dlp.exe\n", "yt-dlp_linux") is None


def _mock_responses(payload: bytes, asset: str, digest_override: str | None = None):
    """Build (sums_response, binary_response) mocks for a two-call requests.get."""
    good = digest_override or hashlib.sha256(payload).hexdigest()
    sums = MagicMock()
    sums.text = f"{good}  {asset}\n"

    binary = MagicMock()
    binary.iter_content.return_value = [payload]
    binary.__enter__.return_value = binary
    binary.__exit__.return_value = False
    return sums, binary


class TestDownloadAndVerify:
    """_download_and_verify only installs a file whose checksum matches."""

    def test_verified_download_is_installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        dest = str(tmp_path / "yt-dlp")
        sums, binary = _mock_responses(b"real-binary-bytes", "yt-dlp_linux")
        with patch("requests.get", side_effect=[sums, binary]):
            assert ytdlp_binary._download_and_verify("yt-dlp_linux", dest) is True
        assert open(dest, "rb").read() == b"real-binary-bytes"
        assert os.access(dest, os.X_OK)

    def test_checksum_mismatch_is_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_binary, "is_windows", lambda: False)
        dest = str(tmp_path / "yt-dlp")
        sums, binary = _mock_responses(b"bytes", "yt-dlp_linux", digest_override="deadbeef")
        with patch("requests.get", side_effect=[sums, binary]):
            assert ytdlp_binary._download_and_verify("yt-dlp_linux", dest) is False
        assert not os.path.exists(dest)
        assert not os.path.exists(dest + ".download")

    def test_missing_checksum_entry_is_rejected(self, tmp_path):
        dest = str(tmp_path / "yt-dlp")
        sums = MagicMock()
        sums.text = "aaa  some-other-asset\n"
        with patch("requests.get", return_value=sums):
            assert ytdlp_binary._download_and_verify("yt-dlp_linux", dest) is False
        assert not os.path.exists(dest)

    def test_network_error_is_swallowed(self, tmp_path):
        dest = str(tmp_path / "yt-dlp")
        with patch("requests.get", side_effect=requests.ConnectionError("boom")):
            assert ytdlp_binary._download_and_verify("yt-dlp_linux", dest) is False
        assert not os.path.exists(dest)
