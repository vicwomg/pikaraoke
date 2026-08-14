"""Unit tests for the KeepAwake helper."""

from contextlib import ExitStack, contextmanager
from unittest import mock

from pikaraoke.lib.keep_awake import (
    _ES_CONTINUOUS,
    _ES_DISPLAY_REQUIRED,
    _ES_SYSTEM_REQUIRED,
    KeepAwake,
    unsupported_reason,
)

_POPEN = "pikaraoke.lib.keep_awake.subprocess.Popen"


@contextmanager
def _platform(target: str, in_docker: bool = False, tool_installed: bool = True):
    """Patch platform detection so only `target` (win/mac/linux) is active."""
    with ExitStack() as stack:
        for name in ("is_windows", "is_macos", "is_linux"):
            stack.enter_context(
                mock.patch(f"pikaraoke.lib.keep_awake.{name}", return_value=(name == target))
            )
        stack.enter_context(
            mock.patch("pikaraoke.lib.keep_awake.is_running_in_docker", return_value=in_docker)
        )
        stack.enter_context(
            mock.patch(
                "pikaraoke.lib.keep_awake.shutil.which",
                return_value="/usr/bin/inhibitor" if tool_installed else None,
            )
        )
        yield


class TestUnsupportedReason:
    def test_windows_is_supported(self):
        with _platform("is_windows"):
            assert unsupported_reason() is None

    def test_container_is_unsupported_even_with_the_tool_installed(self):
        with _platform("is_linux", in_docker=True):
            assert unsupported_reason() == "container"

    def test_missing_tool(self):
        with _platform("is_linux", tool_installed=False):
            assert unsupported_reason() == "missing_tool"

    def test_unknown_platform(self):
        with _platform("none_of_them"):
            assert unsupported_reason() == "unsupported_platform"


class TestMacOS:
    def test_start_launches_caffeinate(self):
        with _platform("is_macos"), mock.patch(_POPEN) as popen:
            ka = KeepAwake()
            ka.start()

        cmd = popen.call_args.args[0]
        assert cmd == ["caffeinate", "-dis"]
        assert ka._process is popen.return_value


class TestLinux:
    def test_start_uses_systemd_inhibit_when_available(self):
        with _platform("is_linux"), mock.patch(_POPEN) as popen:
            ka = KeepAwake()
            ka.start()

        cmd = popen.call_args.args[0]
        assert cmd[0] == "systemd-inhibit"
        assert "--what=idle" in cmd
        assert cmd[-2:] == ["sleep", "infinity"]

    def test_start_warns_and_skips_without_systemd_inhibit(self):
        with _platform("is_linux", tool_installed=False), mock.patch(_POPEN) as popen:
            ka = KeepAwake()
            ka.start()

        popen.assert_not_called()
        assert ka._process is None

    def test_start_skips_in_a_container(self):
        with _platform("is_linux", in_docker=True), mock.patch(_POPEN) as popen:
            ka = KeepAwake()
            ka.start()

        popen.assert_not_called()
        assert ka._process is None

    def test_starting_twice_holds_one_lock(self):
        with _platform("is_linux"), mock.patch(_POPEN) as popen:
            ka = KeepAwake()
            ka.start()
            ka.start()

        popen.assert_called_once()


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
        with _platform("is_macos"), mock.patch(_POPEN) as popen:
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
