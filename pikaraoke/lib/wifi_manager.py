"""Raspberry Pi Wi-Fi control through NetworkManager's ``nmcli`` (terse mode).

The one owner of every ``nmcli`` call. Pi-and-NetworkManager-only: ``supported``
is False everywhere else, and callers hide the surface accordingly.
"""

import logging
import shutil
import string
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager

logger = logging.getLogger(__name__)

_WIFI_INTERFACE = "wlan0"

# NetworkManager's "shared" IPv4 method always hands the AP this address.
HOTSPOT_IP = "10.42.0.1"

_SECRETS_SECTION = "SECRETS"
_PASSWORD_KEY = "hotspot_password"
_PASSWORD_LENGTH = 12

# No characters the Wi-Fi QR must escape (alphanumerics only) nor ones misread off a TV.
_AMBIGUOUS_CHARS = set("0O1lI")
_PASSWORD_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in _AMBIGUOUS_CHARS
)

# nmcli's --wait budget, with the subprocess timeout a little longer so nmcli times out first.
_CONNECT_WAIT_SECONDS = 30
_CONNECT_TIMEOUT_SECONDS = 35
_SCAN_TIMEOUT_SECONDS = 30

# nmcli stderr substrings, matched case-insensitively: a privilege refusal vs a wrong password.
_PRIVILEGE_ERROR_MARKERS = (
    "insufficient privilege",
    "not authorized",
    "access denied",
    "permission denied",
)
_WRONG_PASSWORD_MARKERS = (
    "secrets were required",
    "no secrets",
    "802-11-wireless-security",
    "invalid password",
    "incorrect password",
)


class ConnectResult(Enum):
    """The outcome of a connect attempt the splash and the retry branch on."""

    CONNECTED = "connected"
    WRONG_PASSWORD = "wrong_password"
    NOT_FOUND = "not_found"


@dataclass
class NetworkState:
    """kind is "connected", "hotspot" (the Pi's own), or "none"."""

    kind: str
    network: str | None = None
    ethernet_count: int = 0


def _split_terse(line: str) -> list[str]:
    """Split one ``nmcli -t`` line on its field colons, honouring ``\\:`` escapes."""
    fields: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(line):
        char = line[i]
        if char == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
        elif char == ":":
            fields.append("".join(current))
            current = []
            i += 1
        else:
            current.append(char)
            i += 1
    fields.append("".join(current))
    return fields


def _run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    """Run a command, folding a missing binary or a timeout into a failed result."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        logger.info("Command failed to run: %s (%s)", " ".join(cmd), e)
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


class WifiManager:
    """Owns the Pi's Wi-Fi: its state, nearby networks, the hotspot, and joining."""

    def __init__(
        self,
        preferences: PreferenceManager,
        is_raspberry_pi: bool,
        events: EventSystem | None = None,
    ) -> None:
        self._preferences = preferences
        self._is_raspberry_pi = is_raspberry_pi
        self._events = events
        self.hotspot_name = f"PiKaraoke-{self._wifi_mac_suffix()}"
        self.supported = self._detect_supported()

    def _announce_change(self) -> None:
        """Tell the rest of the app the network moved, so the splash can refresh."""
        if self._events:
            self._events.emit("network_changed")

    @property
    def hotspot_password(self) -> str:
        """The hotspot's WPA2 key, generated once on first read and reused after."""
        stored = self._preferences.get(_PASSWORD_KEY, "", section=_SECRETS_SECTION)
        if not stored:
            import secrets

            stored = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))
            self._preferences.set(_PASSWORD_KEY, stored, section=_SECRETS_SECTION)
        return stored

    def state(self) -> NetworkState:
        """What the Pi is attached to: a network, its own hotspot, or nothing."""
        result = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        if result.returncode != 0:
            return NetworkState(kind="none")

        wifi_network: str | None = None
        ethernet_count = 0
        hotspot_up = False
        for line in result.stdout.splitlines():
            fields = _split_terse(line)
            if len(fields) < 4:
                continue
            _device, dev_type, dev_state, connection = fields[0], fields[1], fields[2], fields[3]
            if dev_state != "connected":
                continue
            if dev_type == "wifi":
                if connection == self.hotspot_name:
                    hotspot_up = True
                else:
                    wifi_network = connection
            elif dev_type == "ethernet":
                ethernet_count += 1

        if hotspot_up:
            return NetworkState(kind="hotspot", network=self.hotspot_name)
        if wifi_network or ethernet_count:
            return NetworkState(
                kind="connected", network=wifi_network, ethernet_count=ethernet_count
            )
        return NetworkState(kind="none")

    def networks(self) -> list[dict]:
        """Nearby networks from a live scan, strongest first, deduplicated, own hotspot omitted."""
        result = _run(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY",
                "device",
                "wifi",
                "list",
                "--rescan",
                "yes",
            ],
            timeout=_SCAN_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return []

        strongest: dict[str, dict] = {}
        for line in result.stdout.splitlines():
            fields = _split_terse(line)
            if len(fields) < 3:
                continue
            ssid, signal, security = fields[0], fields[1], fields[2]
            if not ssid or ssid == self.hotspot_name:
                continue
            try:
                signal_value = int(signal)
            except ValueError:
                signal_value = 0
            existing = strongest.get(ssid)
            if existing is None or signal_value > existing["signal"]:
                strongest[ssid] = {"name": ssid, "signal": signal_value, "security": security}

        return sorted(strongest.values(), key=lambda network: network["signal"], reverse=True)

    def start_hotspot(self) -> bool:
        """Bring up the hotspot, creating its one reusable profile on first use."""
        if self._profile_exists(self.hotspot_name):
            ok = self._run_privileged(["connection", "up", self.hotspot_name]).returncode == 0
        else:
            created = self._run_privileged(
                [
                    "connection",
                    "add",
                    "type",
                    "wifi",
                    "ifname",
                    _WIFI_INTERFACE,
                    "con-name",
                    self.hotspot_name,
                    "autoconnect",
                    "no",
                    "ssid",
                    self.hotspot_name,
                    "802-11-wireless.mode",
                    "ap",
                    "802-11-wireless.band",
                    "bg",
                    "ipv4.method",
                    "shared",
                    "wifi-sec.key-mgmt",
                    "wpa-psk",
                    "wifi-sec.psk",
                    self.hotspot_password,
                ]
            )
            ok = (
                created.returncode == 0
                and self._run_privileged(["connection", "up", self.hotspot_name]).returncode == 0
            )
        if ok:
            self._announce_change()
        return ok

    def stop_hotspot(self) -> bool:
        """Take the hotspot down, leaving its profile in place for next time."""
        ok = self._run_privileged(["connection", "down", self.hotspot_name]).returncode == 0
        if ok:
            self._announce_change()
        return ok

    def connect(self, name: str, password: str = "") -> ConnectResult:
        """Join a network, restarting the hotspot on failure.

        A wrong password deletes the half-made profile (retrying it can never
        work); a not-found keeps it, so a later scan joins it when it appears.
        """
        args = [
            "-w",
            str(_CONNECT_WAIT_SECONDS),
            "device",
            "wifi",
            "connect",
            name,
            "ifname",
            _WIFI_INTERFACE,
        ]
        if password:
            args += ["password", password]

        result = self._run_privileged(args, timeout=_CONNECT_TIMEOUT_SECONDS)
        if result.returncode == 0:
            self._announce_change()
            return ConnectResult.CONNECTED

        if _has_marker(result.stderr, _WRONG_PASSWORD_MARKERS):
            self._run_privileged(["connection", "delete", name])
            outcome = ConnectResult.WRONG_PASSWORD
        else:
            outcome = ConnectResult.NOT_FOUND

        self.start_hotspot()
        return outcome

    def retry_saved_networks(self) -> bool:
        """Join a saved network in range, only with nobody on the hotspot to drop."""
        if self.state().kind != "hotspot":
            return False
        if self._joined_device_count() > 0:
            return False

        saved = set(self._saved_network_names())
        if not saved:
            return False
        in_range = [network["name"] for network in self.networks()]
        candidate = next((name for name in in_range if name in saved), None)
        if candidate is None:
            return False

        return self.connect(candidate) == ConnectResult.CONNECTED

    def _detect_supported(self) -> bool:
        if not self._is_raspberry_pi:
            return False
        if shutil.which("nmcli") is None:
            return False
        # Non-zero means nmcli can't reach the daemon (NetworkManager not running).
        return _run(["nmcli", "-t", "-f", "STATE", "general"], timeout=5).returncode == 0

    def _wifi_mac_suffix(self) -> str:
        """Last four hex digits of the Wi-Fi MAC, so two boxes in a venue differ."""
        if not self._is_raspberry_pi:
            return "0000"
        try:
            mac = Path(f"/sys/class/net/{_WIFI_INTERFACE}/address").read_text().strip()
            return mac.replace(":", "")[-4:].upper() or "0000"
        except OSError:
            return "0000"

    def _profile_exists(self, name: str) -> bool:
        result = _run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
        return name in result.stdout.splitlines()

    def _saved_network_names(self) -> list[str]:
        result = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
        if result.returncode != 0:
            return []
        names = []
        for line in result.stdout.splitlines():
            fields = _split_terse(line)
            if len(fields) < 2:
                continue
            name, conn_type = fields[0], fields[1]
            if conn_type == "802-11-wireless" and name != self.hotspot_name:
                names.append(name)
        return names

    def _joined_device_count(self) -> int:
        result = _run(["iw", "dev", _WIFI_INTERFACE, "station", "dump"])
        if result.returncode != 0:
            # Unknown: assume a guest is joined, so the retry never drops one.
            return 1
        return sum(1 for line in result.stdout.splitlines() if line.strip().startswith("Station"))

    def _run_privileged(
        self, args: list[str], timeout: float = 15.0
    ) -> subprocess.CompletedProcess:
        """Run a state-changing nmcli command, escalating to ``sudo -n`` only if refused.

        Direct serves a desktop session (NetworkManager grants it freely); a kiosk
        service is always refused and ``sudo -n`` carries it. A non-privilege
        failure (a wrong password) is returned as-is, never escalated.
        """
        direct = _run(["nmcli", *args], timeout=timeout)
        if direct.returncode == 0:
            return direct
        if not _has_marker(direct.stderr, _PRIVILEGE_ERROR_MARKERS):
            return direct

        logger.info("nmcli refused without privilege, retrying with sudo -n: %s", " ".join(args))
        escalated = _run(["sudo", "-n", "nmcli", *args], timeout=timeout)
        if escalated.returncode != 0 and _has_marker(escalated.stderr, _PRIVILEGE_ERROR_MARKERS):
            logger.warning(
                "nmcli needs elevated privilege. Grant a polkit rule allowing "
                "org.freedesktop.NetworkManager.* to the user PiKaraoke runs as."
            )
        return escalated
