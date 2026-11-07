"""Tests for the `pikaraoke set-admin-password` subcommand."""

import os

import pytest

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.set_admin_password import set_admin_password_command


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / "config.ini")


def _pipe_stdin(monkeypatch, line):
    """Make the command read `line` from a non-interactive stdin."""
    import io

    stdin = io.StringIO(line)
    stdin.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", stdin)


class TestSet:
    def test_it_sets_a_verifiable_password_on_a_clean_config(self, monkeypatch, config_path):
        _pipe_stdin(monkeypatch, "hunter2\n")

        assert set_admin_password_command(["--config-file-path", config_path]) == 0
        assert AdminAuth(PreferenceManager(config_path)).verify("hunter2")

    def test_it_creates_the_config_file(self, monkeypatch, config_path):
        _pipe_stdin(monkeypatch, "hunter2\n")
        assert not os.path.exists(config_path)

        set_admin_password_command(["--config-file-path", config_path])

        assert os.path.exists(config_path)

    def test_it_targets_the_given_path(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "elsewhere.ini")
        _pipe_stdin(monkeypatch, "hunter2\n")

        set_admin_password_command(["--config-file-path", custom])

        assert not os.path.exists(str(tmp_path / "config.ini"))
        assert AdminAuth(PreferenceManager(custom)).verify("hunter2")

    def test_a_blank_password_leaves_the_box_open(self, monkeypatch, config_path):
        _pipe_stdin(monkeypatch, "\n")

        assert set_admin_password_command(["--config-file-path", config_path]) == 0
        assert not AdminAuth(PreferenceManager(config_path)).is_password_set()


class TestNoOp:
    def test_it_does_not_clobber_an_existing_password(self, monkeypatch, config_path):
        AdminAuth(PreferenceManager(config_path)).set_password("original")
        _pipe_stdin(monkeypatch, "replacement\n")

        assert set_admin_password_command(["--config-file-path", config_path]) == 0
        assert AdminAuth(PreferenceManager(config_path)).verify("original")


class TestStatus:
    def test_exit_1_when_unset(self, config_path):
        assert set_admin_password_command(["--status", "--config-file-path", config_path]) == 1

    def test_exit_0_when_set(self, config_path):
        AdminAuth(PreferenceManager(config_path)).set_password("hunter2")

        assert set_admin_password_command(["--status", "--config-file-path", config_path]) == 0

    def test_status_writes_nothing_on_an_absent_config(self, config_path):
        """The install-state discriminator relies on --status not materialising config.ini."""
        assert not os.path.exists(config_path)

        set_admin_password_command(["--status", "--config-file-path", config_path])

        assert not os.path.exists(config_path)
