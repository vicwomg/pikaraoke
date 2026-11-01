"""Unit tests for WifiManager, with every nmcli/iw subprocess call mocked."""

import string
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.wifi_manager import ConnectResult, WifiManager, _split_terse

# subprocess.run is referenced as module.subprocess.run, so patch it there.
RUN = "pikaraoke.lib.wifi_manager.subprocess.run"


def _done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """A stand-in subprocess.CompletedProcess."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _commands(mock_run) -> list[str]:
    """The joined command line of every subprocess.run call, in order."""
    return [" ".join(call.args[0]) for call in mock_run.call_args_list]


@pytest.fixture
def manager(tmp_path):
    """A manager off a real PreferenceManager; is_raspberry_pi False keeps its
    hotspot name deterministic (PiKaraoke-0000) and runs no nmcli at construction."""
    preferences = PreferenceManager(str(tmp_path / "config.ini"))
    return WifiManager(preferences, is_raspberry_pi=False)


class TestSplitTerse:
    def test_plain_fields(self):
        assert _split_terse("wlan0:wifi:connected:HomeNet") == [
            "wlan0",
            "wifi",
            "connected",
            "HomeNet",
        ]

    def test_escaped_colon_is_part_of_the_field(self):
        assert _split_terse("My\\:Net:70:WPA2") == ["My:Net", "70", "WPA2"]

    def test_trailing_empty_field(self):
        assert _split_terse("wlan0:wifi:disconnected:") == ["wlan0", "wifi", "disconnected", ""]


class TestSupported:
    def test_false_off_a_pi(self, manager):
        assert manager.supported is False

    def test_false_when_nmcli_is_missing(self, tmp_path):
        preferences = PreferenceManager(str(tmp_path / "config.ini"))
        with patch("pikaraoke.lib.wifi_manager.shutil.which", return_value=None):
            manager = WifiManager(preferences, is_raspberry_pi=True)
        assert manager.supported is False

    def test_false_when_networkmanager_is_not_running(self, tmp_path):
        preferences = PreferenceManager(str(tmp_path / "config.ini"))
        with (
            patch("pikaraoke.lib.wifi_manager.shutil.which", return_value="/usr/bin/nmcli"),
            patch(
                RUN,
                return_value=_done(returncode=1, stderr="Error: NetworkManager is not running."),
            ),
        ):
            manager = WifiManager(preferences, is_raspberry_pi=True)
        assert manager.supported is False

    def test_true_on_a_pi_with_networkmanager(self, tmp_path):
        preferences = PreferenceManager(str(tmp_path / "config.ini"))
        with (
            patch("pikaraoke.lib.wifi_manager.shutil.which", return_value="/usr/bin/nmcli"),
            patch(RUN, return_value=_done(stdout="connected")),
            patch.object(Path, "read_text", return_value="b8:27:eb:12:34:56\n"),
        ):
            manager = WifiManager(preferences, is_raspberry_pi=True)
        assert manager.supported is True
        assert manager.hotspot_name == "PiKaraoke-3456"


class TestHotspotPassword:
    def test_generated_from_the_unambiguous_alphabet(self, manager):
        password = manager.hotspot_password
        assert len(password) == 12
        assert all(c in (string.ascii_letters + string.digits) for c in password)
        assert all(c not in "0O1lI" for c in password)

    def test_stable_once_generated(self, manager):
        assert manager.hotspot_password == manager.hotspot_password

    def test_survives_a_restart(self, tmp_path):
        config = str(tmp_path / "config.ini")
        first = WifiManager(PreferenceManager(config), is_raspberry_pi=False).hotspot_password
        second = WifiManager(PreferenceManager(config), is_raspberry_pi=False).hotspot_password
        assert first == second


class TestState:
    def test_connected_over_wifi(self, manager):
        output = "wlan0:wifi:connected:HomeNet\nlo:loopback:unmanaged:\n"
        with patch(RUN, return_value=_done(stdout=output)):
            state = manager.state()
        assert state.kind == "connected"
        assert state.network == "HomeNet"
        assert state.ethernet_count == 0

    def test_connected_over_ethernet(self, manager):
        output = "eth0:ethernet:connected:Wired connection 1\nwlan0:wifi:disconnected:\n"
        with patch(RUN, return_value=_done(stdout=output)):
            state = manager.state()
        assert state.kind == "connected"
        assert state.ethernet_count == 1
        assert state.network is None

    def test_hotspot_up(self, manager):
        with patch(RUN, return_value=_done(stdout="wlan0:wifi:connected:PiKaraoke-0000\n")):
            state = manager.state()
        assert state.kind == "hotspot"
        assert state.network == "PiKaraoke-0000"

    def test_nothing_connected(self, manager):
        output = "wlan0:wifi:disconnected:\neth0:ethernet:unavailable:\n"
        with patch(RUN, return_value=_done(stdout=output)):
            assert manager.state().kind == "none"

    def test_none_when_nmcli_fails(self, manager):
        with patch(RUN, return_value=_done(returncode=1)):
            assert manager.state().kind == "none"


class TestNetworks:
    def test_dedupes_by_name_excludes_own_hotspot_and_sorts_by_signal(self, manager):
        output = (
            "HomeNet:60:WPA2\n"
            "HomeNet:80:WPA2\n"  # stronger duplicate wins
            "Cafe:40:WPA1 WPA2\n"
            "PiKaraoke-0000:0:WPA2\n"  # the Pi's own hotspot, left out
            ":50:--\n"  # hidden/blank SSID, skipped
        )
        with patch(RUN, return_value=_done(stdout=output)) as run:
            networks = manager.networks()
        assert [n["name"] for n in networks] == ["HomeNet", "Cafe"]
        assert networks[0]["signal"] == 80
        assert "--rescan" in _commands(run)[0]

    def test_handles_an_ssid_containing_a_colon(self, manager):
        with patch(RUN, return_value=_done(stdout="My\\:Net:70:WPA2\n")):
            networks = manager.networks()
        assert networks[0]["name"] == "My:Net"

    def test_empty_on_scan_failure(self, manager):
        with patch(RUN, return_value=_done(returncode=1)):
            assert manager.networks() == []


class TestConnect:
    def test_success_makes_no_further_calls(self, manager):
        with patch(RUN, return_value=_done(returncode=0)) as run:
            result = manager.connect("HomeNet", "goodpassword")
        assert result == ConnectResult.CONNECTED
        assert len(run.call_args_list) == 1
        assert "device wifi connect HomeNet" in _commands(run)[0]
        assert "password goodpassword" in _commands(run)[0]

    def test_wrong_password_deletes_profile_and_restarts_hotspot(self, manager):
        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "device wifi connect" in joined:
                return _done(returncode=1, stderr="Error: Secrets were required, but not provided.")
            if "-f NAME connection show" in joined:  # _profile_exists: hotspot absent
                return _done(stdout="Wired connection 1\n")
            return _done(returncode=0)

        with patch(RUN, side_effect=fake_run) as run:
            result = manager.connect("HomeNet", "wrongpassword")

        assert result == ConnectResult.WRONG_PASSWORD
        commands = _commands(run)
        assert any("connection delete HomeNet" in c for c in commands)
        assert any("connection add" in c for c in commands)  # hotspot brought back

    def test_not_found_keeps_profile_and_restarts_hotspot(self, manager):
        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "device wifi connect" in joined:
                return _done(returncode=1, stderr="Error: No network with SSID 'HomeNet' found.")
            if "-f NAME connection show" in joined:
                return _done(stdout="Wired connection 1\n")
            return _done(returncode=0)

        with patch(RUN, side_effect=fake_run) as run:
            result = manager.connect("HomeNet", "password")

        assert result == ConnectResult.NOT_FOUND
        commands = _commands(run)
        assert not any("connection delete" in c for c in commands)
        assert any("connection add" in c for c in commands)  # hotspot brought back


class TestPrivilegeLadder:
    def test_escalates_to_sudo_when_refused(self, manager):
        def fake_run(cmd, **kwargs):
            if cmd[0] == "nmcli":
                return _done(returncode=4, stderr="Error: Insufficient privileges.")
            if cmd[0] == "sudo":
                return _done(returncode=0, stdout="ok")
            return _done()

        with patch(RUN, side_effect=fake_run) as run:
            result = manager._run_privileged(["connection", "up", "X"])

        assert result.returncode == 0
        calls = [call.args[0] for call in run.call_args_list]
        assert calls[0][0] == "nmcli"
        assert calls[1][:3] == ["sudo", "-n", "nmcli"]

    def test_real_error_is_returned_without_escalating(self, manager):
        with patch(
            RUN, return_value=_done(returncode=1, stderr="Error: Secrets were required.")
        ) as run:
            result = manager._run_privileged(["device", "wifi", "connect", "X"])

        assert result.returncode == 1
        assert len(run.call_args_list) == 1  # no sudo attempt


class TestStartStopHotspot:
    def test_creates_profile_on_first_use_then_brings_it_up(self, manager):
        def fake_run(cmd, **kwargs):
            if "-f NAME connection show" in " ".join(cmd):
                return _done(stdout="Wired connection 1\n")  # hotspot profile absent
            return _done(returncode=0)

        with patch(RUN, side_effect=fake_run) as run:
            assert manager.start_hotspot() is True
        commands = _commands(run)
        assert any("connection add" in c for c in commands)
        assert any("connection up PiKaraoke-0000" in c for c in commands)

    def test_reuses_an_existing_profile(self, manager):
        def fake_run(cmd, **kwargs):
            if "-f NAME connection show" in " ".join(cmd):
                return _done(stdout="PiKaraoke-0000\nWired connection 1\n")
            return _done(returncode=0)

        with patch(RUN, side_effect=fake_run) as run:
            assert manager.start_hotspot() is True
        commands = _commands(run)
        assert any("connection up PiKaraoke-0000" in c for c in commands)
        assert not any("connection add" in c for c in commands)

    def test_stop_brings_the_profile_down(self, manager):
        with patch(RUN, return_value=_done(returncode=0)) as run:
            assert manager.stop_hotspot() is True
        assert "connection down PiKaraoke-0000" in _commands(run)[0]


class TestRetrySavedNetworks:
    def _router(self, *, joined: str, saved: str, scan: str, connect_rc: int = 0):
        """A subprocess.run side_effect posing as a hotspot-up Pi."""

        def fake_run(cmd, **kwargs):
            joined_cmd = " ".join(cmd)
            if "device status" in joined_cmd:
                return _done(stdout="wlan0:wifi:connected:PiKaraoke-0000\n")
            if "station dump" in joined_cmd:
                return _done(stdout=joined)
            if "NAME,TYPE" in joined_cmd:
                return _done(stdout=saved)
            if "wifi list" in joined_cmd:
                return _done(stdout=scan)
            if "device wifi connect" in joined_cmd:
                return _done(returncode=connect_rc)
            return _done(returncode=0)

        return fake_run

    def test_joins_a_saved_network_in_range(self, manager):
        router = self._router(
            joined="",  # nobody on the hotspot
            saved="HomeNet:802-11-wireless\nPiKaraoke-0000:802-11-wireless\n",
            scan="HomeNet:70:WPA2\n",
        )
        with patch(RUN, side_effect=router) as run:
            assert manager.retry_saved_networks() is True
        assert any("device wifi connect HomeNet" in c for c in _commands(run))

    def test_does_nothing_while_a_device_is_joined(self, manager):
        router = self._router(
            joined="Station aa:bb:cc:dd:ee:ff (on wlan0)\n",
            saved="HomeNet:802-11-wireless\n",
            scan="HomeNet:70:WPA2\n",
        )
        with patch(RUN, side_effect=router) as run:
            assert manager.retry_saved_networks() is False
        assert not any("device wifi connect" in c for c in _commands(run))

    def test_does_nothing_when_no_saved_network_is_in_range(self, manager):
        router = self._router(
            joined="",
            saved="HomeNet:802-11-wireless\n",
            scan="SomeoneElse:70:WPA2\n",  # HomeNet not in range
        )
        with patch(RUN, side_effect=router) as run:
            assert manager.retry_saved_networks() is False
        assert not any("device wifi connect" in c for c in _commands(run))

    def test_does_nothing_when_not_on_the_hotspot(self, manager):
        def fake_run(cmd, **kwargs):
            if "device status" in " ".join(cmd):
                return _done(stdout="wlan0:wifi:connected:HomeNet\n")  # on a real network
            return _done(returncode=0)

        with patch(RUN, side_effect=fake_run) as run:
            assert manager.retry_saved_networks() is False
        assert not any("device wifi connect" in c for c in _commands(run))
