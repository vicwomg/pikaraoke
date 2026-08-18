"""Unit tests for youtube_dl module."""

import subprocess
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from pikaraoke.lib.youtube_dl import (
    _PREVIEW_ATTEMPTS,
    _serves_bytes,
    build_ytdl_download_command,
    get_stream_url,
    get_youtube_id_from_url,
    get_youtubedl_version,
    upgrade_youtubedl,
)


class TestGetYoutubeIdFromUrl:
    """Tests for the get_youtube_id_from_url function."""

    def test_standard_watch_url(self):
        """Test parsing standard youtube.com/watch URL."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_mobile_watch_url(self):
        """Test parsing m.youtube.com URL."""
        url = "https://m.youtube.com/watch?v=dQw4w9WgXcQ"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_short_url(self):
        """Test parsing youtu.be short URL."""
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_url_with_trailing_params(self):
        """Test that params after the video ID are stripped."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&pp=ygUHa2FyYW9rZQ%3D%3D"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_url_with_leading_params(self):
        """Test parsing when v= is not the first query parameter."""
        url = "https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_short_url_with_params(self):
        """Test parsing short URL with parameters."""
        url = "https://youtu.be/dQw4w9WgXcQ?t=30"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_short_url_with_tracking_params(self):
        """Test parsing share-style short URL with tracking params."""
        url = "https://youtu.be/dQw4w9WgXcQ?si=abcdefghijkl"
        assert get_youtube_id_from_url(url) == "dQw4w9WgXcQ"

    def test_malformed_id_returns_none(self):
        """Test that a value which isn't a valid 11-char ID is rejected."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ?extra=param"
        assert get_youtube_id_from_url(url) is None

    def test_invalid_url_returns_none(self):
        """Test that invalid URL returns None."""
        url = "https://example.com/video"
        assert get_youtube_id_from_url(url) is None

    def test_empty_url_returns_none(self):
        """Test that empty URL returns None."""
        url = ""
        assert get_youtube_id_from_url(url) is None


class TestBuildYtdlDownloadCommand:
    """Tests for the build_ytdl_download_command function."""

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_basic_command(self, mock_js):
        """Test building basic download command."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
        )
        assert cmd[0] == sys.executable
        assert cmd[1] == "-m"
        assert cmd[2] == "yt_dlp"
        assert "-f" in cmd
        assert "-o" in cmd
        output_idx = cmd.index("-o") + 1
        assert cmd[output_idx].endswith("%(title)s---%(id)s.%(ext)s")
        assert "/songs" in cmd[output_idx]
        assert "https://www.youtube.com/watch?v=test123" in cmd

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_high_quality_format(self, mock_js):
        """Test that high quality uses correct format string.

        The codec filter is the guard: ext!=webm admits AV1, which would then be
        transcoded on every playback rather than stream-copied.
        """
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
            high_quality=True,
        )
        format_idx = cmd.index("-f") + 1
        assert "bestvideo" in cmd[format_idx]
        assert "vcodec^=avc1" in cmd[format_idx]
        assert "1080" in cmd[format_idx]

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_standard_quality_format(self, mock_js):
        """Test that standard quality uses mp4 format."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
            high_quality=False,
        )
        format_idx = cmd.index("-f") + 1
        assert cmd[format_idx] == "mp4"

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_with_proxy(self, mock_js):
        """Test command with proxy setting."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
            youtubedl_proxy="http://proxy:8080",
        )
        assert "--proxy" in cmd
        proxy_idx = cmd.index("--proxy") + 1
        assert cmd[proxy_idx] == "http://proxy:8080"

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_with_additional_args(self, mock_js):
        """Test command with additional arguments."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
            additional_args="--no-playlist --age-limit 18",
        )
        assert "--no-playlist" in cmd
        assert "--age-limit" in cmd
        assert "18" in cmd

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value="node")
    def test_with_js_runtime_node(self, mock_js):
        """Test that node JS runtime is added to command."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
        )
        assert "--js-runtimes" in cmd
        js_idx = cmd.index("--js-runtimes") + 1
        assert cmd[js_idx] == "node"

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value="deno")
    def test_deno_not_added(self, mock_js):
        """Test that deno JS runtime is NOT added (it's yt-dlp default)."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
        )
        assert "--js-runtimes" not in cmd

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value="bun")
    def test_with_js_runtime_bun(self, mock_js):
        """Test that bun JS runtime is added to command."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
        )
        assert "--js-runtimes" in cmd
        js_idx = cmd.index("--js-runtimes") + 1
        assert cmd[js_idx] == "bun"

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_vcodec_sort(self, mock_js):
        """Test that h264 codec sorting is included."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
        )
        assert "-S" in cmd
        sort_idx = cmd.index("-S") + 1
        assert cmd[sort_idx] == "vcodec:h264"

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_url_is_last_argument(self, mock_js):
        """Test that video URL is always the last argument."""
        cmd = build_ytdl_download_command(
            video_url="https://www.youtube.com/watch?v=test123",
            download_path="/songs",
            youtubedl_proxy="http://proxy:8080",
            additional_args="--no-playlist",
        )
        assert cmd[-1] == "https://www.youtube.com/watch?v=test123"


class TestGetStreamUrl:
    """Tests for the get_stream_url function."""

    @staticmethod
    def _run_with_output(stdout: bytes, returncode: int = 0):
        return patch(
            "subprocess.run",
            return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=b""),
        )

    @staticmethod
    def _serving(*results: bool):
        return patch("pikaraoke.lib.youtube_dl._serves_bytes", side_effect=results)

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_requests_progressive_format_only(self, mock_js):
        """Test that the format selector excludes HLS and single-track formats."""
        with self._run_with_output(b"https://rr1.googlevideo.com/videoplayback?x=1\n") as mock_run:
            with self._serving(True):
                get_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            cmd = mock_run.call_args[0][0]
            fmt = cmd[cmd.index("-f") + 1]
            assert "[protocol=https]" in fmt
            assert "[vcodec!=none][acodec!=none]" in fmt

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_returns_progressive_url(self, mock_js):
        """Test that a verified progressive URL is returned."""
        url = "https://rr1.googlevideo.com/videoplayback?itag=18"
        with self._run_with_output(url.encode() + b"\n"), self._serving(True):
            assert get_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == url

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_rejects_hls_manifest(self, mock_js):
        """Test that an m3u8 manifest is rejected; browsers other than Safari can't play it."""
        manifest = (
            "https://manifest.googlevideo.com/api/manifest/hls_playlist/itag/91/playlist/index.m3u8"
        )
        with self._run_with_output(manifest.encode() + b"\n"):
            assert get_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_returns_none_on_failure(self, mock_js):
        """Test that a non-zero yt-dlp exit returns None."""
        with self._run_with_output(b"", returncode=1):
            assert get_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_re_resolves_when_stream_refused(self, mock_js):
        """Test that a refused URL is discarded and a freshly resolved one returned."""
        refused = b"https://rr1.googlevideo.com/videoplayback?itag=18&dead=1\n"
        working = "https://rr2.googlevideo.com/videoplayback?itag=18&live=1"
        runs = [
            MagicMock(returncode=0, stdout=refused, stderr=b""),
            MagicMock(returncode=0, stdout=working.encode() + b"\n", stderr=b""),
        ]
        with patch("subprocess.run", side_effect=runs), self._serving(False, True):
            assert get_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == working

    @patch("pikaraoke.lib.youtube_dl.get_installed_js_runtime", return_value=None)
    def test_returns_none_when_every_attempt_refused(self, mock_js):
        """Test that a URL the browser could not play is never handed back."""
        url = "https://rr1.googlevideo.com/videoplayback?itag=18"
        with self._run_with_output(url.encode() + b"\n"):
            with self._serving(*[False] * _PREVIEW_ATTEMPTS):
                assert get_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None


class TestServesBytes:
    """Tests for the _serves_bytes stream check."""

    @staticmethod
    def _response(status_code: int):
        return patch("requests.get", return_value=MagicMock(status_code=status_code))

    def test_accepts_partial_content(self):
        """Test that a ranged request answered with 206 counts as playable."""
        with self._response(206):
            assert _serves_bytes("https://rr1.googlevideo.com/videoplayback") is True

    def test_rejects_forbidden(self):
        """Test that YouTube refusing the stream counts as unplayable."""
        with self._response(403):
            assert _serves_bytes("https://rr1.googlevideo.com/videoplayback") is False

    def test_rejects_unreachable_host(self):
        """Test that a transport failure counts as unplayable rather than raising."""
        with patch("requests.get", side_effect=requests.ConnectionError("boom")):
            assert _serves_bytes("https://rr1.googlevideo.com/videoplayback") is False


class TestGetYoutubedlVersion:
    """Tests for the get_youtubedl_version function."""

    def test_returns_version_string(self):
        """Test that version string is returned."""
        with patch("subprocess.check_output", return_value=b"2024.01.01\n"):
            result = get_youtubedl_version()
            assert result == "2024.01.01"

    def test_calls_with_version_flag(self):
        """Test that --version flag is passed."""
        with patch("subprocess.check_output", return_value=b"2024.01.01") as mock_check:
            get_youtubedl_version()
            mock_check.assert_called_once_with([sys.executable, "-m", "yt_dlp", "--version"])


class TestUpgradeYoutubedl:
    """Tests for the upgrade_youtubedl function."""

    @patch("pikaraoke.lib.youtube_dl.get_youtubedl_version", return_value="2024.02.01")
    def test_successful_self_upgrade(self, mock_version):
        """Test successful self-upgrade via yt-dlp -U."""
        with patch("subprocess.check_output", return_value=b"Updated to 2024.02.01"):
            result = upgrade_youtubedl()
            assert result == "2024.02.01"

    def test_fallback_to_pip_upgrade(self):
        """Test fallback to pip when yt-dlp -U suggests pip (in venv, no --break-system-packages)."""
        pip_message = b"You installed yt-dlp with pip or using the wheel from PyPi"
        error = subprocess.CalledProcessError(1, "yt-dlp", pip_message)
        error.output = pip_message

        with patch(
            "pikaraoke.lib.youtube_dl.get_youtubedl_version", return_value="2024.02.01"
        ), patch("shutil.which", return_value=None), patch(
            "subprocess.check_output"
        ) as mock_check, patch(
            "pikaraoke.lib.youtube_dl.sys.prefix", "/venv"
        ), patch(
            "pikaraoke.lib.youtube_dl.sys.base_prefix", "/different"
        ):
            # First call raises error suggesting pip, second call succeeds
            mock_check.side_effect = [error, b"Successfully installed yt-dlp"]
            result = upgrade_youtubedl()

            assert result == "2024.02.01"
            # Check sys.executable -m pip was called (in a venv, no --break-system-packages)
            assert mock_check.call_count == 2
            second_call_args = mock_check.call_args_list[1][0][0]
            assert "-m" in second_call_args
            assert "pip" in second_call_args
            assert "--break-system-packages" not in second_call_args

    def test_pip_upgrade_adds_break_system_packages_for_system_install(self):
        """Test that --break-system-packages is added when in system install."""
        pip_message = b"You installed yt-dlp with pip or using the wheel from PyPi"
        error = subprocess.CalledProcessError(1, "yt-dlp", pip_message)
        error.output = pip_message

        with patch(
            "pikaraoke.lib.youtube_dl.get_youtubedl_version", return_value="2024.02.01"
        ), patch("shutil.which", return_value=None), patch(
            "subprocess.check_output"
        ) as mock_check, patch(
            "pikaraoke.lib.youtube_dl.sys.prefix", "/usr"
        ), patch(
            "pikaraoke.lib.youtube_dl.sys.base_prefix", "/usr"
        ):
            # First call raises error suggesting pip, second call succeeds
            mock_check.side_effect = [error, b"Successfully installed yt-dlp"]
            result = upgrade_youtubedl()

            assert result == "2024.02.01"
            # Check --break-system-packages was added for system install
            assert mock_check.call_count == 2
            second_call_args = mock_check.call_args_list[1][0][0]
            assert "-m" in second_call_args
            assert "pip" in second_call_args
            assert "--break-system-packages" in second_call_args

    @patch("pikaraoke.lib.youtube_dl.get_youtubedl_version", return_value="2024.01.01")
    def test_returns_version_after_upgrade(self, mock_version):
        """Test that current version is returned after upgrade."""
        with patch("subprocess.check_output", return_value=b"Already up to date"):
            result = upgrade_youtubedl()
            assert result == "2024.01.01"
            mock_version.assert_called_once_with()
