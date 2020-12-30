import logging
import os
import subprocess


class OMXClient:
    def __init__(self, path=None, adev=None, dual_screen=False):
        # Handle omxplayer paths
        if path == None:
            self.path = "/usr/bin/omxplayer"
        else:
            self.path = path

        if adev == None:
            self.adev = "both"
        else:
            self.adev = adev

        if dual_screen:
            self.dual_screen = True
        else:
            self.dual_screen = False

        self.paused = False

        self.volume_offset = 0
        self.process = None

    def play_file(self, file_path, additional_parameters=None):
        logging.info("Playing video in omxplayer: " + file_path)
        self.kill()
        cmd = [
            self.path,
            file_path,
            "--blank",
            "-o",
            self.adev,
            "--vol",
            str(self.volume_offset),
            "--font-size",
            str(25),
        ]
        if self.dual_screen:
            cmd += ["--display", "7"]
       
        logging.debug("Player command: " + " ".join(cmd))
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.paused = False

    def pause(self):
        if (not self.paused):
            self.process.stdin.write("p".encode("utf-8"))
            self.process.stdin.flush()
            self.paused = True

    def play(self):
        if (self.paused):
            self.process.stdin.write("p".encode("utf-8"))
            self.process.stdin.flush()
            self.paused = False

    def stop(self):
        self.process.stdin.write("q".encode("utf-8"))
        self.process.stdin.flush()
        self.paused = False

    def restart(self):
        self.process.stdin.write("i".encode("utf-8"))
        self.process.stdin.flush()
        self.paused = False

    def vol_up(self):
        logging.info("Volume up")
        self.process.stdin.write("=".encode("utf-8"))
        self.process.stdin.flush()
        self.volume_offset += 300

    def vol_down(self):
        logging.info("Volume down")
        self.process.stdin.write("-".encode("utf-8"))
        self.volume_offset -= 300

    def kill(self):
        try:
            self.process.kill()
            logging.debug("Killing old omxplayer processes")
            player_kill = ["killall", "omxplayer.bin"]
            FNULL = open(os.devnull, "w")
            subprocess.Popen(
                player_kill, stdin=subprocess.PIPE, stdout=FNULL, stderr=FNULL
            )
            self.paused = False
        except (OSError, AttributeError) as e:
            logging.error(e)
            return

    def is_running(self):
        return (
            self.process != None and self.process.poll() == None
        ) 

    def is_playing(self):
        return (
            self.process != None and self.process.poll() == None and self.paused == False
        )

    def is_paused(self):
        return self.paused

    def get_volume(self):
        return self.volume_offset

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
