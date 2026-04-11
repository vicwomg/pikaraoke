"""Unit tests for network module."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.network import (
    _get_ip_android,
    _get_ip_default,
    _get_ip_via_psutil,
    _get_ip_via_udp_socket,
    _get_ip_windows,
    get_ip,
)


class TestGetIpViaUdpSocket:
    """Tests for the _get_ip_via_udp_socket function."""

    def test_returns_ip_address(self):
        """Test that it returns an IP address."""
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("192.168.1.100", 12345)

        with patch("socket.socket", return_value=mock_socket):
            result = _get_ip_via_udp_socket("8.8.8.8")
            assert result == "192.168.1.100"

    def test_returns_localhost_on_error(self):
        """Test that it returns localhost on connection error."""
        mock_socket = MagicMock()
        mock_socket.connect.side_effect = Exception("Network unreachable")

        with patch("socket.socket", return_value=mock_socket):
            result = _get_ip_via_udp_socket("8.8.8.8")
            assert result == "127.0.0.1"

    def test_socket_is_closed(self):
        """Test that socket is always closed."""
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("192.168.1.100", 12345)

        with patch("socket.socket", return_value=mock_socket):
            _get_ip_via_udp_socket("8.8.8.8")
            mock_socket.close.assert_called_once()


class TestGetIpWindows:
    """Tests for the _get_ip_windows function."""

    def test_returns_ip_from_hostname(self):
        """Test getting IP from hostname."""
        with patch("socket.gethostname", return_value="my-pc"):
            with patch("socket.gethostbyname", return_value="192.168.1.50"):
                result = _get_ip_windows()
                assert result == "192.168.1.50"

    def test_fallback_to_udp_socket_on_localhost(self):
        """Test fallback when hostname returns localhost."""
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("192.168.1.100", 12345)

        with patch("socket.gethostname", return_value="localhost"):
            with patch("socket.gethostbyname", return_value="127.0.0.1"):
                with patch("socket.socket", return_value=mock_socket):
                    result = _get_ip_windows()
                    assert result == "192.168.1.100"

    def test_returns_localhost_on_error(self):
        """Test returning localhost on exception."""
        with patch("socket.gethostname", side_effect=Exception("Error")):
            result = _get_ip_windows()
            assert result == "127.0.0.1"


class TestGetIpAndroid:
    """Tests for the _get_ip_android function."""

    def test_returns_ip_from_ifconfig(self):
        """Test getting IP from ifconfig command."""
        with patch("subprocess.check_output", return_value=b"192.168.1.200\n"):
            result = _get_ip_android()
            assert result == "192.168.1.200"

    def test_returns_localhost_on_empty(self):
        """Test returning localhost when ifconfig returns empty."""
        with patch("subprocess.check_output", return_value=b""):
            result = _get_ip_android()
            assert result == "127.0.0.1"

    def test_returns_localhost_on_error(self):
        """Test returning localhost on subprocess error."""
        with patch("subprocess.check_output", side_effect=Exception("Command failed")):
            result = _get_ip_android()
            assert result == "127.0.0.1"


class TestGetIpDefault:
    """Tests for the _get_ip_default function."""

    def test_calls_udp_socket_with_correct_target(self):
        """Test that it calls UDP socket with correct target IP."""
        with patch(
            "pikaraoke.lib.network._get_ip_via_udp_socket", return_value="192.168.1.100"
        ) as mock:
            result = _get_ip_default()
            mock.assert_called_once_with("10.255.255.255")
            assert result == "192.168.1.100"


class TestGetIp:
    """Tests for the main get_ip function."""

    def test_uses_psutil_when_available(self):
        """Test that psutil method is used first."""
        with patch("pikaraoke.lib.network._get_ip_via_psutil", return_value="192.168.1.100"):
            result = get_ip("linux")
            assert result == "192.168.1.100"

    def test_fallback_to_android_method(self):
        """Test fallback to Android method when psutil fails."""
        with patch("pikaraoke.lib.network._get_ip_via_psutil", side_effect=Exception("No psutil")):
            with patch("pikaraoke.lib.network._get_ip_android", return_value="192.168.1.200"):
                result = get_ip("android")
                assert result == "192.168.1.200"

    def test_fallback_to_windows_method(self):
        """Test fallback to Windows method when psutil fails."""
        with patch("pikaraoke.lib.network._get_ip_via_psutil", side_effect=Exception("No psutil")):
            with patch("pikaraoke.lib.network._get_ip_windows", return_value="192.168.1.150"):
                result = get_ip("windows")
                assert result == "192.168.1.150"

    def test_fallback_to_default_method(self):
        """Test fallback to default method for Linux/macOS."""
        with patch("pikaraoke.lib.network._get_ip_via_psutil", side_effect=Exception("No psutil")):
            with patch("pikaraoke.lib.network._get_ip_default", return_value="192.168.1.75"):
                result = get_ip("linux")
                assert result == "192.168.1.75"


class TestGetIpViaPsutil:
    """Tests for the _get_ip_via_psutil function."""

    def _create_mock_addr(self, family, address):
        """Helper to create a mock address object."""
        addr = MagicMock()
        addr.family = family
        addr.address = address
        return addr

    def test_returns_ethernet_ip(self):
        """Test that ethernet interface IP is returned with highest priority."""
        mock_addrs = {
            "eth0": [self._create_mock_addr(socket.AF_INET, "192.168.1.100")],
            "wlan0": [self._create_mock_addr(socket.AF_INET, "192.168.1.200")],
        }
        mock_stats = {
            "eth0": MagicMock(isup=True),
            "wlan0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.100"

    def test_returns_wifi_ip_when_no_ethernet(self):
        """Test that WiFi IP is returned when no ethernet available."""
        mock_addrs = {
            "wlan0": [self._create_mock_addr(socket.AF_INET, "192.168.1.200")],
        }
        mock_stats = {
            "wlan0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.200"

    def test_skips_virtual_interfaces(self):
        """Test that virtual interfaces are skipped."""
        mock_addrs = {
            "docker0": [self._create_mock_addr(socket.AF_INET, "172.17.0.1")],
            "veth123": [self._create_mock_addr(socket.AF_INET, "172.18.0.1")],
            "eth0": [self._create_mock_addr(socket.AF_INET, "192.168.1.100")],
        }
        mock_stats = {
            "docker0": MagicMock(isup=True),
            "veth123": MagicMock(isup=True),
            "eth0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.100"

    def test_skips_localhost(self):
        """Test that localhost addresses are skipped."""
        mock_addrs = {
            "lo": [self._create_mock_addr(socket.AF_INET, "127.0.0.1")],
            "eth0": [self._create_mock_addr(socket.AF_INET, "192.168.1.100")],
        }
        mock_stats = {
            "lo": MagicMock(isup=True),
            "eth0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.100"

    def test_skips_apipa_addresses(self):
        """Test that APIPA (169.254.x.x) addresses are skipped."""
        mock_addrs = {
            "eth0": [self._create_mock_addr(socket.AF_INET, "169.254.1.1")],
            "wlan0": [self._create_mock_addr(socket.AF_INET, "192.168.1.200")],
        }
        mock_stats = {
            "eth0": MagicMock(isup=True),
            "wlan0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.200"

    def test_skips_interface_that_is_down(self):
        """Test that interfaces that are down are skipped."""
        mock_addrs = {
            "eth0": [self._create_mock_addr(socket.AF_INET, "192.168.1.100")],
            "wlan0": [self._create_mock_addr(socket.AF_INET, "192.168.1.200")],
        }
        mock_stats = {
            "eth0": MagicMock(isup=False),
            "wlan0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.200"

    def test_skips_ipv6_addresses(self):
        """Test that IPv6 addresses are skipped."""
        mock_addrs = {
            "eth0": [
                self._create_mock_addr(socket.AF_INET6, "fe80::1"),
                self._create_mock_addr(socket.AF_INET, "192.168.1.100"),
            ],
        }
        mock_stats = {
            "eth0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.100"

    def test_raises_when_no_suitable_interface(self):
        """Test that exception is raised when no suitable interface found."""
        mock_addrs = {
            "lo": [self._create_mock_addr(socket.AF_INET, "127.0.0.1")],
        }
        mock_stats = {
            "lo": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            with pytest.raises(Exception, match="No suitable network interface"):
                _get_ip_via_psutil()

    def test_wifi_interface_names(self):
        """Test that various WiFi interface names are recognized."""
        for iface_name in ["wlan0", "Wi-Fi", "wifi0"]:
            mock_addrs = {
                iface_name: [self._create_mock_addr(socket.AF_INET, "192.168.1.200")],
                "other0": [self._create_mock_addr(socket.AF_INET, "10.0.0.1")],
            }
            mock_stats = {
                iface_name: MagicMock(isup=True),
                "other0": MagicMock(isup=True),
            }

            mock_psutil = MagicMock()
            mock_psutil.net_if_addrs.return_value = mock_addrs
            mock_psutil.net_if_stats.return_value = mock_stats

            with patch.dict("sys.modules", {"psutil": mock_psutil}):
                result = _get_ip_via_psutil()
                # WiFi should have higher priority than "other"
                assert result == "192.168.1.200"

    def test_en_interface_recognized_as_ethernet(self):
        """Test that 'en' interfaces (macOS) are recognized as ethernet."""
        mock_addrs = {
            "en0": [self._create_mock_addr(socket.AF_INET, "192.168.1.100")],
            "other0": [self._create_mock_addr(socket.AF_INET, "10.0.0.1")],
        }
        mock_stats = {
            "en0": MagicMock(isup=True),
            "other0": MagicMock(isup=True),
        }

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = mock_addrs
        mock_psutil.net_if_stats.return_value = mock_stats

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_ip_via_psutil()
            assert result == "192.168.1.100"
