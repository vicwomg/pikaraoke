""" 
This script is only for Raspberry Pi

It uses Pipewire / Wireplumber to:
- Get the list of audio sinks (hdmi, headphone, bluetooth...)
- Set the audio output
- Set up or down output volume

To install the necessary components run:
`Sudo apt install pipewire pipewire-pulse pipewire-jack pipewire-alsa pipewire-audio -y`

Based on the Wireplumber docs
https://wiki.archlinux.org/title/WirePlumber
"""

import subprocess
import html
import logging

def get_audio_sinks():
    try:
        # Executes the wpctl status command and captures the output
        logging.debug("Getting audio sinks")

        command = ['wpctl', 'status']
        result = run_commands(command)

        # logging.debug("Audio sinks: " + result)

        # Control variables
        in_audio_section = False
        in_sinks_section = False
        sinks = []

        # Processes each line of the output
        for line in result.splitlines():
            line = line.strip()
            
            # Detects the start and end of the audio section
            if not in_audio_section and line == "Audio":
                in_audio_section = True
                continue
            elif in_audio_section and line == "Video":
                in_audio_section = False
                continue

            # Detects the start and end of the "Sinks" subsection
            if in_audio_section and "Sinks:" in line:
                in_sinks_section = True
                continue
            elif in_audio_section and "Sink endpoints:" in line:
                in_sinks_section = False
                continue

            # Captures only valid sink lines
            elif in_sinks_section:
                line = str.split(line)

                # Checks if it's an empty line
                if len(line) < 3:
                    continue

                # newline = []
                default=False

                # Checks if it's the default sink line
                if "*" in line:
                    # newline.append("1")
                    default=True
                    line.remove("*")
                else:
                    default=False
                    # newline.append("0")

                number = line[1].strip('.') # Gets the number
                name = ' '.join(line[2:-2]) # Gets the name

                volume = get_volume(float(line[-1].strip('[]'))) # Gets the volume

                newline = {"default": default, "number": number, "volume": volume, "name": name}
                   
                sinks.append(newline)

        return sinks

    except Exception as e:
        logging.debug(f"Error getting audio sinks: {e}")
        return []

def set_default_audio_sink(sink_number):
    logging.debug(f"Setting default audio sink to: {sink_number}")
    command = ['wpctl', 'set-default', sink_number]
    return run_commands(command)


def get_volume(volume):
    logging.debug(f"Getting volume: {volume}")
    volume = volume * 100

    if volume % 10 != 0:
        volume = round_sink_volume(volume)
    return int(volume)

# As we increase and decrease the volume by 0.1, we need to round it
def round_sink_volume(volume, sink_number = "default"):
    logging.debug(f"Rounding volume: {volume}")
    volume = round(volume/100, 1)
    sink = "@DEFAULT_AUDIO_SINK@" if sink_number == "default" else sink_number
    command = ['wpctl', 'set-volume', sink, str(volume)]
    run_commands(command)
    return volume * 100

def set_device_vol_up(sink_number = "default"):
    sink = "@DEFAULT_AUDIO_SINK@" if sink_number == "default" else sink_number
    command = ['wpctl', 'set-volume', '-l', '1.5', sink, "0.1+"]
    return run_commands(command)

def set_device_vol_down(sink_number = "default"):
    sink = "@DEFAULT_AUDIO_SINK@" if sink_number == "default" else sink_number
    command = ['wpctl', 'set-volume', '-l', '1.5', sink, "0.1-"]
    return run_commands(command)

# Function that runs the commands using subprocess
def run_commands(command):
    logging.debug("Running commands")

    try:
        # Executes the command, captures, and returns the output
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        output = html.unescape(result.stdout)
        return output
    except subprocess.CalledProcessError as e:
        # Captures specific subprocess errors
        logging.debug(f"Error executing the command: {e}")
        return None
    except Exception as e:
        # Captures any other errors
        logging.debug(f"An error occurred running the command: {e}")
        return None
