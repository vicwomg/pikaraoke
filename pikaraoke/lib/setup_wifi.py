import time

from pywifi import Profile, const
from pywifi.iface import Interface


def get_current_wifi(iface: Interface):
    status = iface.status()
    if status == const.IFACE_CONNECTED:
        iface.scan()
        time.sleep(0.1)
        scan_results = iface.scan_results()
        for network in scan_results:
            if network.signal > -70:
                return network.ssid
    return None


def wait_for_connection(iface: Interface, timeout: int = 15, interval: float = 0.5) -> bool:
    """
    Polling to wait for Wi-Fi connection to succeed and avoid using time.sleep to wait for a long time
    """
    start = time.time()
    while time.time() - start < timeout:
        status = iface.status()
        if status == const.IFACE_CONNECTED:
            return True
        time.sleep(interval)
    return False


def get_all_wifi(iface: Interface):
    """
    Scans for nearby Wi-Fi and attempts to establish a correct connection setup
    """
    iface.scan()
    time.sleep(1)
    results = iface.scan_results()
    table = []
    for network in results:
        if network.ssid == "":
            network.ssid = "Hidden Wi-Fi"
        record = {
            "ssid": network.ssid,
            "auth": network.auth,
            "akm": network.akm,
            "cipher": network.cipher,
        }
        table.append(record)
    return table


def connect_to_wifi(iface: Interface, ssid: str, password: str = ""):
    """
    Try to connect based on Wi-Fi information
    """
    try:
        iface.scan()
        time.sleep(2)
        results = iface.scan_results()
        target = None
        for network in results:
            if network.ssid == ssid:
                target = network
                break

        if target is None:
            raise ValueError(f"The specified SSID could not be found:{ssid}")

        profile: Profile = Profile()

        profile.ssid = ssid
        # auth
        profile.auth = target.auth[0] if target.auth else const.AUTH_ALG_OPEN
        # akm
        profile.akm = [target.akm[0]] if target.akm else [const.AKM_TYPE_NONE]
        # cipher
        profile.cipher = target.cipher if target.cipher else const.CIPHER_TYPE_CCMP
        # key
        if profile.akm[0] != const.AKM_TYPE_NONE:
            profile.key = password
        iface.remove_all_network_profiles()
        profile = iface.add_network_profile(profile)
        iface.connect(profile)
    except ValueError as e:
        print(f"❌ An error occurred: {e}")
        return False
    # Polling and waiting for connection result
    return wait_for_connection(iface)
