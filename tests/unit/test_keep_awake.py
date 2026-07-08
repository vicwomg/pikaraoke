"""Unit tests for the KeepAwake helper."""

from contextlib import ExitStack, contextmanager
from unittest import mock

from pikaraoke.lib.keep_awake import (
    _ES_CONTINUOUS,
    _ES_DISPLAY_REQUIRED,
    _ES_SYSTEM_REQUIRED,
    KeepAwake,
)


@contextmanager
def _platform(target: str):
    """Patch platform detectors so only `target` (win/mac/linux) is active."""
    with ExitStack() as stack:
        for name in ("is_windows", "is_macos", "is_linux"):
            stack.enter_context(
                mock.patch(f"pikaraoke.lib.keep_awake.{name}", return_value=(name == target))
            )
        yield


class TestMacOS:
    def test_start_launches_caffeinate(self):
        with _platform("is_macos"), mock.patch(
            "pikaraoke.lib.keep_awake.subprocess.Popen"
        ) as popen:
            ka = KeepAwake()
            ka.start()

        cmd = popen.call_args.args[0]
        assert cmd == ["caffeinate", "-dis"]
        assert ka._process is popen.return_value


class TestLinux:
    def test_start_uses_systemd_inhibit_when_available(self):
        with _platform("is_linux"), mock.patch(
            "pikaraoke.lib.keep_awake.shutil.which", return_value="/usr/bin/systemd-inhibit"
        ), mock.patch("pikaraoke.lib.keep_awake.subprocess.Popen") as popen:
            ka = KeepAwake()
            ka.start()

        cmd = popen.call_args.args[0]
        assert cmd[0] == "systemd-inhibit"
        assert "--what=idle" in cmd
        assert cmd[-2:] == ["sleep", "infinity"]

    def test_start_warns_and_skips_without_systemd_inhibit(self):
        with _platform("is_linux"), mock.patch(
            "pikaraoke.lib.keep_awake.shutil.which", return_value=None
        ), mock.patch("pikaraoke.lib.keep_awake.subprocess.Popen") as popen:
            ka = KeepAwake()
            ka.start()

        popen.assert_not_called()
        assert ka._process is None


class TestWindows:
    def test_start_sets_execution_state(self):
        windll = mock.MagicMock()
        windll.kernel32.SetThreadExecutionState.return_value = 1
        with _platform("is_windows"), mock.patch("ctypes.windll", windll, create=True):
            ka = KeepAwake()
            ka.start()

        windll.kernel32.SetThreadExecutionState.assert_called_once_with(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
        )

    def test_stop_clears_execution_state(self):
        windll = mock.MagicMock()
        with _platform("is_windows"), mock.patch("ctypes.windll", windll, create=True):
            ka = KeepAwake()
            ka.stop()

        windll.kernel32.SetThreadExecutionState.assert_called_once_with(_ES_CONTINUOUS)


class TestStop:
    def test_stop_terminates_subprocess(self):
        with _platform("is_macos"), mock.patch(
            "pikaraoke.lib.keep_awake.subprocess.Popen"
        ) as popen:
            ka = KeepAwake()
            ka.start()
            process = popen.return_value
            ka.stop()

        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        assert ka._process is None

    def test_stop_without_start_is_safe(self):
        with _platform("is_macos"):
            ka = KeepAwake()
            ka.stop()  # should not raise
        assert ka._process is None
