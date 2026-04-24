"""Unit tests for get_platform module."""

import ntpath
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pikaraoke.lib.get_platform import (
    get_data_directory,
    get_default_dl_dir,
    get_installed_js_runtime,
    get_os_version,
    get_platform,
    has_js_runtime,
    is_android,
    is_raspberry_pi,
    is_windows,
)


class TestIsRaspberryPi:
    """Tests for the is_raspberry_pi function."""

    def test_raspberry_pi_detected(self):
        """Test detection when running on Raspberry Pi."""
        mock_file = mock_open(read_data="Raspberry Pi 4 Model B Rev 1.2")
        with patch("io.open", mock_file):
            assert is_raspberry_pi() is True

    def test_raspberry_pi_lowercase(self):
        """Test detection with lowercase model string."""
        mock_file = mock_open(read_data="raspberry pi 3 model b")
        with patch("io.open", mock_file):
            assert is_raspberry_pi() is True

    def test_not_raspberry_pi(self):
        """Test detection on non-Pi hardware."""
        mock_file = mock_open(read_data="Generic ARM Board")
        with patch("io.open", mock_file):
            assert is_raspberry_pi() is False

    def test_file_not_found(self):
        """Test when device-tree file doesn't exist."""
        with patch("io.open", side_effect=FileNotFoundError):
            assert is_raspberry_pi() is False


class TestIsAndroid:
    """Tests for the is_android function."""

    def test_android_detected(self):
        """Test detection when running on Android."""
        with patch(
            "os.path.exists", side_effect=lambda p: p in ["/system/app/", "/system/priv-app"]
        ):
            assert is_android() is True

    def test_not_android_missing_app(self):
        """Test when /system/app/ is missing."""
        with patch("os.path.exists", side_effect=lambda p: p == "/system/priv-app"):
            assert is_android() is False

    def test_not_android_missing_priv_app(self):
        """Test when /system/priv-app is missing."""
        with patch("os.path.exists", side_effect=lambda p: p == "/system/app/"):
            assert is_android() is False

    def test_not_android(self):
        """Test when neither Android path exists."""
        with patch("os.path.exists", return_value=False):
            assert is_android() is False


class TestIsWindows:
    """Tests for the is_windows function."""

    def test_windows_detected(self):
        """Test detection when running on Windows."""
        with patch("sys.platform", "win32"):
            assert is_windows() is True

    def test_not_windows(self):
        """Test detection when running on Linux."""
        with patch("sys.platform", "linux"):
            assert is_windows() is False


class TestGetInstalledJsRuntime:
    """Tests for the get_installed_js_runtime function."""

    def test_deno_found(self):
        """Test when deno is installed."""
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/deno" if x == "deno" else None):
            assert get_installed_js_runtime() == "deno"

    def test_node_found(self):
        """Test when node is installed (but not deno)."""

        def which_mock(cmd):
            if cmd == "node":
                return "/usr/bin/node"
            return None

        with patch("shutil.which", side_effect=which_mock):
            assert get_installed_js_runtime() == "node"

    def test_bun_found(self):
        """Test when bun is installed (but not deno/node)."""

        def which_mock(cmd):
            if cmd == "bun":
                return "/usr/bin/bun"
            return None

        with patch("shutil.which", side_effect=which_mock):
            assert get_installed_js_runtime() == "bun"

    def test_quickjs_found(self):
        """Test when quickjs is installed (but not others)."""

        def which_mock(cmd):
            if cmd == "quickjs":
                return "/usr/bin/quickjs"
            return None

        with patch("shutil.which", side_effect=which_mock):
            assert get_installed_js_runtime() == "quickjs"

    def test_none_found(self):
        """Test when no JS runtime is installed."""
        with patch("shutil.which", return_value=None):
            assert get_installed_js_runtime() is None

    def test_priority_order(self):
        """Test that deno takes priority over others."""
        with patch("shutil.which", return_value="/usr/bin/something"):
            assert get_installed_js_runtime() == "deno"


class TestHasJsRuntime:
    """Tests for the has_js_runtime function."""

    def test_has_runtime(self):
        """Test when a JS runtime is available."""
        with patch("pikaraoke.lib.get_platform.get_installed_js_runtime", return_value="node"):
            assert has_js_runtime() is True

    def test_no_runtime(self):
        """Test when no JS runtime is available."""
        with patch("pikaraoke.lib.get_platform.get_installed_js_runtime", return_value=None):
            assert has_js_runtime() is False


class TestGetPlatform:
    """Tests for the get_platform function."""

    def test_osx_platform(self):
        """Test macOS detection."""
        with patch("sys.platform", "darwin"):
            with patch("pikaraoke.lib.get_platform.is_android", return_value=False):
                with patch("pikaraoke.lib.get_platform.is_raspberry_pi", return_value=False):
                    assert get_platform() == "osx"

    def test_windows_platform(self):
        """Test Windows detection."""
        # Ensure sys.platform is win32 so the 'linux' check in get_platform doesn't catch it early
        with patch("sys.platform", "win32"):
            with patch("pikaraoke.lib.get_platform.is_windows", return_value=True):
                with patch("pikaraoke.lib.get_platform.is_android", return_value=False):
                    with patch("pikaraoke.lib.get_platform.is_raspberry_pi", return_value=False):
                        assert get_platform() == "windows"

    def test_linux_platform(self):
        """Test Linux detection."""
        with patch("sys.platform", "linux"):
            with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
                with patch("pikaraoke.lib.get_platform.is_android", return_value=False):
                    with patch("pikaraoke.lib.get_platform.is_raspberry_pi", return_value=False):
                        assert get_platform() == "linux"

    def test_android_platform(self):
        """Test Android detection (takes priority over linux)."""
        with patch("sys.platform", "linux"):
            with patch("pikaraoke.lib.get_platform.is_android", return_value=True):
                assert get_platform() == "android"

    def test_raspberry_pi_platform(self):
        """Test Raspberry Pi detection returns the exact device-tree model string."""
        mock_file = mock_open(read_data="Raspberry Pi 4 Model B Rev 1.2")
        with patch("sys.platform", "linux"):
            with patch("pikaraoke.lib.get_platform.is_android", return_value=False):
                with patch("pikaraoke.lib.get_platform.is_raspberry_pi", return_value=True):
                    with patch("builtins.open", mock_file):
                        assert get_platform() == "Raspberry Pi 4 Model B Rev 1.2"

    def test_unknown_platform(self):
        """Test unknown platform detection."""
        with patch("sys.platform", "freebsd"):
            with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
                with patch("pikaraoke.lib.get_platform.is_android", return_value=False):
                    with patch("pikaraoke.lib.get_platform.is_raspberry_pi", return_value=False):
                        assert get_platform() == "unknown"


class TestGetDefaultDlDir:
    """Tests for the get_default_dl_dir function."""

    def test_raspberry_pi_default(self):
        """Pi defaults to the data dir, same as other unix platforms."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
            with patch("os.path.exists", return_value=False):
                result = get_default_dl_dir("Raspberry Pi 4")
                assert result == os.path.expanduser("~/.pikaraoke/songs")

    def test_windows_default(self):
        """Windows default lives under %APPDATA%."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=True):
            with patch("os.path.exists", return_value=False):
                with patch.dict(os.environ, {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}):
                    result = get_default_dl_dir("windows")
                    assert result == os.path.join(
                        "C:\\Users\\Test\\AppData\\Roaming", "pikaraoke", "songs"
                    )

    def test_windows_legacy_exists(self):
        """Windows uses legacy dir if it exists."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=True):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.expanduser", return_value="C:\\Users\\test\\pikaraoke-songs"):
                    result = get_default_dl_dir("windows")
                    assert "pikaraoke-songs" in result

    def test_linux_default(self):
        """Linux default lives under ~/.pikaraoke."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
            with patch("os.path.exists", return_value=False):
                result = get_default_dl_dir("linux")
                assert result == os.path.expanduser("~/.pikaraoke/songs")

    def test_linux_legacy_exists(self):
        """Linux returns a legacy dir when one is present."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
            with patch("os.path.exists", return_value=True):
                result = get_default_dl_dir("linux")
                assert result == os.path.expanduser("~/pikaraoke-songs")

    def test_osx_default(self):
        """macOS default lives under ~/.pikaraoke."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
            with patch("os.path.exists", return_value=False):
                result = get_default_dl_dir("osx")
                assert result == os.path.expanduser("~/.pikaraoke/songs")


class TestGetDataDirectory:
    """Tests for the get_data_directory function."""

    def test_windows_path(self):
        """Test that Windows returns the APPDATA path."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=True):
            with patch.dict(os.environ, {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}):
                # Mock os.path to be a MagicMock to avoid real FS interaction and cross-contamination
                with patch("pikaraoke.lib.get_platform.os.path") as mock_path:
                    # Configure mock to behave like ntpath (Windows)
                    mock_path.join.side_effect = ntpath.join
                    mock_path.exists.return_value = True  # Simulate dir exists

                    result = get_data_directory()
                    assert result == "C:\\Users\\Test\\AppData\\Roaming\\pikaraoke"

    def test_windows_path_creation(self):
        """Test that Windows creates the directory if missing."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=True):
            with patch.dict(os.environ, {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}):
                with patch("os.makedirs") as mock_makedirs:
                    # Mock os.path completely to avoid real FS interaction
                    with patch("pikaraoke.lib.get_platform.os.path") as mock_path:
                        # Configure mock to behave like ntpath (Windows)
                        mock_path.join.side_effect = ntpath.join
                        mock_path.exists.return_value = False  # Simulate dir MISSING

                        get_data_directory()

                        expected_path = "C:\\Users\\Test\\AppData\\Roaming\\pikaraoke"
                        mock_makedirs.assert_called_once_with(expected_path)

    def test_linux_path(self):
        """Test that Linux/Mac returns the home directory path."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
            with patch("os.path.expanduser", return_value="/home/test/.pikaraoke"):
                with patch("os.path.exists", return_value=True):
                    result = get_data_directory()
                    assert result == "/home/test/.pikaraoke"

    def test_linux_path_creation(self):
        """Test that Linux creates the missing data directory at ~/.pikaraoke."""
        with patch("pikaraoke.lib.get_platform.is_windows", return_value=False):
            with patch("os.path.expanduser", return_value="/home/test/.pikaraoke"):
                with patch("os.path.exists", return_value=False):
                    with patch("os.makedirs") as mock_makedirs:
                        get_data_directory()
                        mock_makedirs.assert_called_once_with("/home/test/.pikaraoke")


class TestGetOsVersion:
    """Tests for the get_os_version function."""

    def test_returns_version_string(self):
        """Test that it returns a version string."""
        with patch("platform.version", return_value="5.15.0-generic"):
            result = get_os_version()
            assert result == "5.15.0-generic"


class TestGetAcceleratorBackend:
    """Tests for get_accelerator_backend — the US-41 settings readout.

    Torch is optional; on the dev box it's installed, but a prod Pi
    build may not have it. The helper must return a renderable dict in
    every case so the template doesn't need try/except around .backend.
    """

    def test_torch_missing_returns_none(self):
        """Without torch installed the readout is 'none'/'torch not installed'."""
        import builtins

        from pikaraoke.lib import get_platform as gp

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no torch")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = gp.get_accelerator_backend()

        assert result == {"backend": "none", "detail": "torch not installed"}

    def test_cuda_path(self):
        """CUDA available -> backend=CUDA with device name."""
        from pikaraoke.lib import get_platform as gp

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.get_device_name.return_value = "NVIDIA GeForce RTX 4090"

        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = gp.get_accelerator_backend()

        assert result == {"backend": "CUDA", "detail": "NVIDIA GeForce RTX 4090"}

    def test_mps_path(self):
        """MPS available (no CUDA) -> backend=MPS with Apple Silicon label."""
        from pikaraoke.lib import get_platform as gp

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = gp.get_accelerator_backend()

        assert result == {"backend": "MPS", "detail": "Apple Silicon"}

    def test_cpu_fallback(self):
        """Neither CUDA nor MPS -> CPU + hint."""
        from pikaraoke.lib import get_platform as gp

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = gp.get_accelerator_backend()

        assert result == {"backend": "CPU", "detail": "no GPU detected"}


class TestGetLibraryVersions:
    """Tests for get_library_versions — US-41 version footprint.

    The contract is that every expected package key is present in the
    returned dict regardless of install state, so the template can do
    ``{% if library_versions.whisperx %}`` safely.
    """

    def test_all_keys_present(self):
        """Returned dict always includes whisperx and demucs."""
        from pikaraoke.lib.get_platform import get_library_versions

        result = get_library_versions()
        assert set(result.keys()) == {"whisperx", "demucs"}

    def test_missing_package_yields_none(self):
        """A PackageNotFoundError from importlib.metadata maps to None."""
        from importlib.metadata import PackageNotFoundError

        from pikaraoke.lib import get_platform as gp

        def fake_version(pkg):
            raise PackageNotFoundError(pkg)

        with patch("pikaraoke.lib.get_platform.version", create=True):
            # Patch at the call site inside the function. importlib.metadata
            # is imported inside get_library_versions, so we have to patch
            # the module's lookup rather than the import alias.
            with patch("importlib.metadata.version", side_effect=fake_version):
                result = gp.get_library_versions()

        assert result == {"whisperx": None, "demucs": None}
