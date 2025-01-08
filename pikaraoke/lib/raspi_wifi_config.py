import os

raspi_wifi_config_ip = "10.0.0.1"
raspi_wifi_conf_file = "/etc/raspiwifi/raspiwifi.conf"
raspi_wifi_config_installed = os.path.exists(raspi_wifi_conf_file)


def get_raspi_wifi_conf_vals():
    """Extract values from the RaspiWiFi configuration file."""
    f = open(raspi_wifi_conf_file, "r")

    # Define default values.
    #
    # References:
    # - https://github.com/jasbur/RaspiWiFi/blob/master/initial_setup.py (see defaults in input prompts)
    # - https://github.com/jasbur/RaspiWiFi/blob/master/libs/reset_device/static_files/raspiwifi.conf
    #
    server_port = "80"
    ssid_prefix = "RaspiWiFi Setup"
    ssl_enabled = "0"

    # Override the default values according to the configuration file.
    for line in f.readlines():
        if "server_port=" in line:
            server_port = line.split("t=")[1].strip()
        elif "ssid_prefix=" in line:
            ssid_prefix = line.split("x=")[1].strip()
        elif "ssl_enabled=" in line:
            ssl_enabled = line.split("d=")[1].strip()
        elif "wpa_key=" in line:
            wpa_key = line.split("wpa_key=")[1].strip()

    return (server_port, ssid_prefix, ssl_enabled, wpa_key)


def get_raspi_wifi_text(url):
    # Wifi is setup as a Access Point
    ap_name = ""
    ap_password = ""

    if os.path.isfile(raspi_wifi_conf_file):
        conf_vals = get_raspi_wifi_conf_vals()
        ap_name = conf_vals[1]
        ap_password = conf_vals[3]

    if len(ap_password) > 0:
        text = [
            f"Wifi Network: {ap_name} Password: {ap_password}",
            f"Configure Wifi: {url.rpartition(':')[0]}",
        ]
    else:
        text = [f"Wifi Network: {ap_name}", f"Configure Wifi: {url.rpartition(':',1)[0]}"]

    return text
