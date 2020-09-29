import logging
import os
import random
import string
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from threading import Timer

import requests

from get_platform import get_platform


class VLCClient:
    def __init__(self, port=5002, path=None):

        # HTTP remote control server
        self.http_password = "".join(
            [random.choice(string.ascii_letters + string.digits) for n in range(32)]
        )
        self.port = port
        self.http_endpoint = "http://localhost:%s/requests/status.xml" % self.port
        self.http_command_endpoint = self.http_endpoint + "?command="
        self.is_transposing = False

        # Handle vlc paths
        self.platform = get_platform()
        if path == None:
            if self.platform == "osx":
                self.path = "/Applications/VLC.app/Contents/MacOS/VLC"
            elif self.platform == "windows":
                alt_vlc_path = r"C:\\Program Files (x86)\\VideoLAN\VLC\\vlc.exe"
                if os.path.isfile(alt_vlc_path):
                    self.path = alt_vlc_path
                else:
                    self.path = r"C:\\Program Files\\VideoLAN\VLC\\vlc.exe"
            else:
                self.path = "/usr/bin/vlc"
        else:
            self.path = path

        # Set up command line args
        self.cmd_base = [
            self.path,
            "-f",
            "--play-and-exit",
            "--extraintf",
            "http",
            "--http-port",
            "%s" % self.port,
            "--http-password",
            self.http_password,
            "--no-embedded-video",
            "--no-keyboard-events",
            "--no-mouse-events",
            "--mouse-hide-timeout",
            "0",
            "--video-on-top",
            "--no-video-title",
            "--mouse-hide-timeout",
            "0",
        ]
        if self.platform == "osx":
            self.cmd_base += [
                "--no-macosx-show-playback-buttons",
                "--no-macosx-show-playmode-buttons",
                "--no-macosx-interfacestyle",
                "--macosx-nativefullscreenmode",
                "--macosx-continue-playback",
                "0",
            ]

        logging.info("VLC command base: " + " ".join(self.cmd_base))

        self.volume_offset = 10
        self.process = None

    def play_file(self, file_path, additional_parameters=None):
        if self.is_running():
            self.kill()
        if self.platform == "windows":
            file_path = r"{}".format(file_path)
        if additional_parameters == None:
            command = self.cmd_base + [file_path]
        else:
            command = self.cmd_base + additional_parameters + [file_path]
        self.process = subprocess.Popen(
            command, shell=(self.platform == "windows"), stdin=subprocess.PIPE
        )

    def play_file_transpose(self, file_path, semitones):
        params = [
            "--audio-filter",
            "scaletempo_pitch",
            "--pitch-shift",
            "%s" % semitones,
        ]
        # pi sounds bad otherwise (CPU not sufficient for maxed out settings)
        if self.platform == "raspberry_pi":
            params += [
                "--speex-resampler-quality",
                "4",
                "--src-converter-type",
                "3",
            ]
        else:
            params += [
                "--speex-resampler-quality",
                "10",
                "--src-converter-type",
                "0",
            ]
        self.is_transposing = True
        logging.debug("Transposing file...")
        self.play_file(file_path, params)
        s = Timer(2.0, self.set_transposing_false)
        s.start()

    def set_transposing_false(self):
        self.is_transposing = False
        logging.debug("Transposing complete")

    def command(self, command):
        if self.is_running():
            url = self.http_command_endpoint + command
            request = requests.get(url, auth=("", self.http_password))
            return request
        else:
            logging.error("No active VLC process. Could not run command: " + command)

    def pause(self):
        return self.command("pl_pause")

    def play(self):
        return self.command("pl_play")

    def stop(self):
        try:
            return self.command("pl_stop")
        except:
            e = sys.exc_info()[0]
            logging.warn(
                "Track stop: server may have shut down before http return code received: %s"
                % e
            )
            return

    def restart(self):
        logging.info(self.command("seek&val=0"))
        return self.command("seek&val=0")

    def vol_up(self):
        return self.command("volume&val=%d" % (self.get_volume() + self.volume_offset))

    def vol_down(self):
        return self.command("volume&val=%d" % (self.get_volume() - self.volume_offset))

    def kill(self):
        try:
            self.process.kill()
        except (OSError, AttributeError):
            return

    def is_running(self):
        return (
            self.process != None and self.process.poll() == None
        ) or self.is_transposing

    def is_playing(self):
        if self.is_running():
            status = self.get_status()
            state = status.find("state").text
            return state == "playing"
        else:
            return False

    def get_volume(self):
        status = self.get_status()
        return int(status.find("volume").text)

    def get_status(self):
        url = self.http_endpoint
        request = requests.get(url, auth=("", self.http_password))
        return ET.fromstring(request.text)

    def run(self):
        try:
            while True:
                pass
        except KeyboardInterrupt:
            self.kill()


# if __name__ == "__main__":
#     k = VLCClient()
#     k.play_file("/path/to/file.mp4")
#     time.sleep(2)
#     k.pause()
#     k.vol_up()
#     k.vol_up()
#     time.sleep(2)
#     k.vol_down()
#     k.vol_down()
#     time.sleep(2)
#     k.play()
#     time.sleep(2)
#     k.stop()
