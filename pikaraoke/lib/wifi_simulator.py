"""In-memory Wi-Fi simulator for developing the feature off a Raspberry Pi.

Enabled by setting the PIKARAOKE_FAKE_WIFI environment variable. It forces
``supported`` True on any platform and fakes every state, scan and connect in
memory, so the Settings section, the splash panel and the live socket updates
can be exercised on an online Windows or Mac box without a Pi, without nmcli,
and without ever touching the real network connection.

Temporary dev aid - delete this file and its instantiation in karaoke.py before
the feature ships.

It starts on a simulated hotspot. connect() rules let a tester drive every
branch by hand:
  - password "fail"           -> wrong password (the hotspot returns)
  - a name not in the scan list -> not found, saved (the hotspot returns)
  - anything else             -> connected to that network
"""

from pikaraoke.lib.wifi_manager import ConnectResult, NetworkState, WifiManager


class FakeWifiManager(WifiManager):
    _FAKE_NETWORKS = (
        {"name": "Venue Guest WiFi", "signal": 82, "security": "WPA2"},
        {"name": "HomeNet", "signal": 64, "security": "WPA2"},
        {"name": "Coffee Shop", "signal": 38, "security": "WPA1 WPA2"},
    )

    def __init__(self, preferences, is_raspberry_pi, events=None):
        super().__init__(preferences, is_raspberry_pi, events=events)
        self._kind = "hotspot"
        self._network: str | None = None
        self._saved: set[str] = set()

    def _detect_supported(self) -> bool:
        return True

    def _wifi_mac_suffix(self) -> str:
        return "0000"

    def state(self) -> NetworkState:
        if self._kind == "hotspot":
            return NetworkState(kind="hotspot", network=self.hotspot_name)
        if self._kind == "connected":
            return NetworkState(kind="connected", network=self._network)
        return NetworkState(kind="none")

    def networks(self) -> list[dict]:
        return [dict(network) for network in self._FAKE_NETWORKS]

    def saved_networks(self) -> list[str]:
        return ["HomeNet", "Coffee Shop"]

    def start_hotspot(self) -> bool:
        self._kind = "hotspot"
        self._network = None
        self._announce_change()
        return True

    def stop_hotspot(self) -> bool:
        self._kind = "none"
        self._announce_change()
        return True

    def connect(self, name: str, password: str = "") -> ConnectResult:
        known = {network["name"] for network in self._FAKE_NETWORKS}
        if password == "fail":
            self._kind = "hotspot"
            self._announce_change()
            return ConnectResult.WRONG_PASSWORD
        if name not in known:
            self._saved.add(name)
            self._kind = "hotspot"
            self._announce_change()
            return ConnectResult.NOT_FOUND
        self._kind = "connected"
        self._network = name
        self._announce_change()
        return ConnectResult.CONNECTED

    def reconcile(self) -> bool:
        if self._kind != "hotspot":
            return False
        known = {network["name"] for network in self._FAKE_NETWORKS}
        candidate = next((name for name in self._saved if name in known), None)
        if candidate is None:
            return False
        return self.connect(candidate) == ConnectResult.CONNECTED

    def _joined_device_count(self) -> int:
        return 0
