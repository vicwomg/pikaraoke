import logging
import os
import random
import string
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import requests


class VLCClient:
    def __init__(self, port=8080, path=None):
        # OS detection
        self.is_raspberry_pi = os.uname()[4][:3] == "arm"
        self.is_osx = sys.platform == "darwin"

        # HTTP remote control server
        self.http_password = "".join(
            [random.choice(string.ascii_letters + string.digits) for n in xrange(32)]
        )
        self.port = port
        self.http_endpoint = "http://localhost:%s/requests/status.xml" % self.port
        self.http_command_endpoint = self.http_endpoint + "?command="

        # Handle vlc paths
        if path == None:
            if self.is_osx:
                self.path = "/Applications/VLC.app/Contents/MacOS/VLC"
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
            "--http-password",
            self.http_password,
            "--no-embedded-video",
            "--no-keyboard-events",
            "--no-mouse-events",
            "--mouse-hide-timeout",
            "0",
            "--video-on-top",
        ]
        if self.is_osx:
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

    def play_file(self, file_path):
        if self.is_vlc_running():
            self.kill()
        command = self.cmd_base + [file_path]
        print(self.http_password)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE)

    def command(self, command):
        if self.process and self.process.poll() == None:
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
        return self.command("pl_stop")

    def restart(self):
        return self.command("seek&val=0")

    def vol_up(self):
        return self.command("volume&val=%d" % (self.get_volume() + self.volume_offset))

    def vol_down(self):
        return self.command("volume&val=%d" % (self.get_volume() - self.volume_offset))

    def kill(self):
        self.process.kill()

    def is_vlc_running(self):
        return self.process != None and self.process.poll() == None

    def is_playing(self):
        if self.is_vlc_running():
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
