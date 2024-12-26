"""
This script is only for Raspberry Pi

It uses bluetoothctl to:
- Sacan for bluetooth devices
- Pair a bluetooth device
- Remove a bluetooth device

To do:
1. When looking for known devices, it should compare the file btdevices.txt with the list of known devices in bluetoothctl ("bluetoothctl devices"), to prevent
a device already paired from not being seen as it's not in te bluetooth file.
2. In the btdevices file, create a line with the new files, with the date/time of the addition, because if the the user scans for devicesbefore the "DiscoveryTimeout"
setted in the bluetooth configuration file ("/etc/bluetooth/main.conf") it will not be shown.
"""

import ast
import configparser
import logging
import os
import subprocess
import time
from time import localtime, strftime

now = strftime("%Y-%m-%d %H:%M:%S", localtime())
path = os.path.dirname(__file__)
file = os.path.join(path, "btdevices.txt")
section = "DEVICES"
key = "known"
config_obj = configparser.ConfigParser()


# Grabs the list of known devices
def get_known_devices():
    logging.debug("Getting known devices")
    if os.path.exists(file):
        config_obj.read(file)
        if section in config_obj:
            try:
                known_devices = ast.literal_eval(config_obj[section][key])
                return ["ok", known_devices]
            except (ValueError, SyntaxError) as e:
                return ["error", f"parsing_error: {str(e)}"]
        else:
            return ["error", "no_section"]
    else:
        return ["error", "no_file"]


# Adds the device to the file
def add_known_device(device):
    logging.debug(f'Adding known device: {device["name"]}')
    devices = get_known_devices()
    device = {"mac": device["mac"], "name": device["name"]}

    if devices[0] != "ok":
        devices[1] = []
        if section not in config_obj:
            config_obj.add_section(section)
    else:
        for existing_device in devices[1]:
            if existing_device["mac"] == device["mac"]:
                logging.info("Device already exists: %s", device)
                return

    devices[1].append(device)
    config_obj[section][key] = str(devices[1])
    with open(file, "w") as configfile:
        config_obj.write(configfile)

    logging.info("Device added successfully: %s", device["name"])
    return


# Remove the device from the file
def remove_known_device(device):
    logging.debug(f'Removing known device: {device["name"]}')
    if os.path.exists(file):
        devices = get_known_devices()
        if devices[0] == "ok":
            # devices[1] = [item for item in devices[1] if item[1] != device]
            devices[1] = [item for item in devices[1] if item["mac"] != device["mac"]]
            config_obj[section][key] = str(devices[1])
            with open(file, "w") as configfile:
                config_obj.write(configfile)
            return ["ok", "Device removed successfully"]
    return ["error", "Something went wrong while removing the device"]


# Runs the bt commands
def run_bluetoothctl_command(process, command):
    # Inicia o processo bluetoothctl
    process.stdin.write(command)
    process.stdin.flush()
    return


# Receive a list of commands and run them one by one
def run_commands(process, commands):
    for command in commands:
        run_bluetoothctl_command(process, command)
        # time.sleep(2)
    return


# Scan for bluetooth devices
def scan_and_get_bt_devices(scan_time=10):
    process = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    run_commands(process, ["scan on\n"])

    time.sleep(scan_time)

    run_commands(process, ["scan off\n", "devices\n"])

    devices_output, _ = process.communicate()

    # Filtra e captura apenas as linhas que contenham 'Device', ou seja, os dispositivos reais
    devices_new = []
    devices_known = []

    lines = devices_output.splitlines()

    for line in lines:
        if "Device" in line:
            line = line.split()
            if line[0] == "\x1b[K[\x01\x1b[0;92m\x02NEW\x01\x1b[0m\x02]":
                if line[2] != line[3].replace("-", ":"):
                    newline = {
                        "status": "new",
                        "mac": line[2],
                        "name": " ".join(line[3:]),
                        "date": now,
                    }
                    devices_new.append(newline)

    devices_known = [] if get_known_devices()[0] == "error" else get_known_devices()[1]

    return {"new": devices_new, "known": devices_known}


# Connect to a bluetooth device (Pair, connect and trust)
def connect_to_bt_device(device):
    tries = 0
    success = False

    while tries != 5:
        tries += 1

        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.stdin.write("scan on\n")
        process.stdin.flush()
        time.sleep(10)
        process.stdin.write(f'pair {device["mac"]}\n')
        process.stdin.flush()
        time.sleep(2 + tries)
        process.stdin.write(f'connect {device["mac"]}\n')
        process.stdin.flush()
        time.sleep(3)
        process.stdin.write(f'trust {device["mac"]}\n')
        process.stdin.flush()
        result, _ = process.communicate()

        lines = result.splitlines()

        for line in lines:
            print(line)
            if "Connection successful" in line:
                success = True
                tries = 5
                break

    if success:
        logging.debug("Successfully connected to " + device["name"])
        add_known_device(device)
        return ["ok", "Successfully connected to " + device["name"]]
    else:
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.stdin.write(f'remove {device["mac"]}\n')
        process.stdin.flush()
        result, _ = process.communicate()
        logging.debug(f'Fail to connect to {device["name"]}')
        return ["error", f'Fail to connect to {device["name"]}']


# Remove the device from bluetooth controller and the file
def remove_bt_device(device):
    logging.debug(f'Removing {device["name"]} - MAC: {device["mac"]}')
    try:
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.stdin.write(f'remove {device["mac"]}\n')
        process.stdin.flush()
        result, _ = process.communicate()
        return remove_known_device(device)
    except Exception as e:
        logging.debug(f'Error removing {device["name"]}: {e}')
        return ["error", f'Error removing {device["name"]}: {e}']
