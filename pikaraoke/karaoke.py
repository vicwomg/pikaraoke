import json
import logging
import os
import random
import socket
import subprocess
import time
from pathlib import Path
from queue import Empty, Queue
from subprocess import check_output
from threading import Thread
from typing import TypedDict
from urllib.parse import urlparse

import ffmpeg
import qrcode
import yt_dlp
from unidecode import unidecode

from pikaraoke.lib.file_resolver import FileResolver
from pikaraoke.lib.get_platform import get_platform

logger = logging.getLogger(__name__)

YT_DLP_VERSION = yt_dlp.version.__version__
YT_DLP_CMD = "yt-dlp"


class SongQueue(TypedDict):
    user: str
    file: str
    title: str
    semitones: float


# Support function for reading  lines from ffmpeg stderr without blocking
def enqueue_output(out, queue: Queue):
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


def decode_ignore(input: bytes):
    return input.decode("utf-8", "ignore").strip()


class Karaoke:
    raspi_wifi_config_ip = "10.0.0.1"
    raspi_wifi_conf_file = Path("/etc/raspiwifi/raspiwifi.conf")
    raspi_wifi_config_installed = raspi_wifi_conf_file.is_file()

    queue: list[SongQueue] = []
    available_songs: list[str] = []

    # These all get sent to the /nowplaying endpoint for client-side polling
    now_playing = None
    now_playing_filename = None
    now_playing_user = None
    now_playing_transpose = 0
    now_playing_url = None
    now_playing_command = None

    is_playing = False
    is_paused = True
    process = None
    qr_code_path = None
    volume = None
    loop_interval = 500  # in milliseconds
    base_path = Path(__file__).parent
    screensaver_timeout = 300  # in seconds

    ffmpeg_process = None

    def __init__(
        self,
        logo_path: Path,
        port=5555,
        ffmpeg_port=5556,
        download_path: Path = "/usr/lib/pikaraoke/songs",
        hide_url=False,
        hide_raspiwifi_instructions=False,
        hide_splash_screen=False,
        high_quality=False,
        volume=0.85,
        log_level=logging.DEBUG,
        splash_delay=2,
        hide_overlay=False,
        screensaver_timeout=300,
        url=None,
        ffmpeg_url=None,
        prefer_hostname=True,
    ):
        # override with supplied constructor args if provided
        self.logo_path = logo_path
        self.port = port
        self.ffmpeg_port = ffmpeg_port
        self.hide_url = hide_url
        self.hide_raspiwifi_instructions = hide_raspiwifi_instructions
        self.hide_splash_screen = hide_splash_screen
        self.download_path = download_path
        self.high_quality = high_quality
        self.splash_delay = int(splash_delay)
        self.volume = volume
        self.hide_overlay = hide_overlay
        self.screensaver_timeout = screensaver_timeout
        self.url_override = url
        self.prefer_hostname = prefer_hostname

        # other initializations
        self.platform = get_platform()
        self.screen = None

        # Get /tmp dir
        try:
            fr = FileResolver("logo.png")
            self._tmp_dir = fr.tmp_dir
        except Exception as e:
            logger.error("Failed to find /tmp dir. Using '/tmp/pikaraoke'")
            self._tmp_dir = Path("/tmp/pikaraoke")

        self._tmp_dir.parent.mkdir(parents=True, exist_ok=True)

        logging.debug(
            f"http port: {self.port}\n"
            f"ffmpeg port {self.ffmpeg_port}\n"
            f"hide URL: {self.hide_url}\n"
            f"prefer hostname: {self.prefer_hostname}\n"
            f"url override: {self.url_override}\n"
            f"hide RaspiWiFi instructions: {self.hide_raspiwifi_instructions}\n"
            f"headless (hide splash): {self.hide_splash_screen}\n"
            f"splash_delay: {self.splash_delay}\n"
            f"screensaver_timeout: {self.screensaver_timeout}\n"
            f"high quality video: {self.high_quality}\n"
            f"download path: {self.download_path}\n"
            f"default volume: {self.volume}\n"
            f"logo path: {self.logo_path}\n"
            f"log_level: {log_level}\n"
            f"hide overlay: {self.hide_overlay}\n"
            f"base_path: {self.base_path}\n"
            f"tmp dir: {self._tmp_dir}\n"
        )

        # Generate connection URL and QR code,
        if self.platform.is_rpi():
            # retry in case pi is still starting up
            # and doesn't have an IP yet (occurs when launched from /etc/rc.local)
            end_time = int(time.time()) + 30
            while int(time.time()) < end_time:
                addresses_str = check_output(["hostname", "-I"]).strip().decode("utf-8", "ignore")
                addresses = addresses_str.split(" ")
                self.ip = addresses[0]
                if not self.is_network_connected():
                    logging.debug("Couldn't get IP, retrying....")
                else:
                    break
        else:
            self.ip = self._get_ip()

        logging.debug("IP address (for QR code and splash screen): " + self.ip)

        if self.url_override != None:
            logging.debug("Overriding URL with " + self.url_override)
            self.url = self.url_override
        else:
            if self.prefer_hostname:
                self.url = f"http://{socket.getfqdn().lower()}:{self.port}"
            else:
                self.url = f"http://{self.ip}:{self.port}"
        self.url_parsed = urlparse(self.url)
        if ffmpeg_url is None:
            self.ffmpeg_url = (
                f"{self.url_parsed.scheme}://{self.url_parsed.hostname}:{self.ffmpeg_port}"
            )
        else:
            self.ffmpeg_url = ffmpeg_url

        # get songs from download_path
        self.get_available_songs()
        self.generate_qr_code()

    # Other ip-getting methods are unreliable and sometimes return 127.0.0.1
    # https://stackoverflow.com/a/28950776
    def _get_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(("10.255.255.255", 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = "127.0.0.1"
        finally:
            s.close()
        return IP

    def get_raspi_wifi_conf_vals(self):
        """Extract values from the RaspiWiFi configuration file."""
        f = self.raspi_wifi_conf_file.open()

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

        return (server_port, ssid_prefix, ssl_enabled)

    def is_network_connected(self):
        return not len(self.ip) < 7

    def generate_qr_code(self):
        logging.debug("Generating URL QR code")
        qr = qrcode.QRCode(
            version=1,
            box_size=1,
            border=4,
        )
        qr.add_data(self.url)
        qr.make()
        img = qr.make_image()

        self.qr_code_path = self._tmp_dir.joinpath("qrcode.png")
        self.qr_code_path.parent.mkdir(parents=True, exist_ok=True)

        logger.debug(f"{self.qr_code_path=}")
        img.save(str(self.qr_code_path))

    def get_search_results(self, textToSearch):
        logging.info("Searching YouTube for: " + textToSearch)
        num_results = 10
        yt_search = 'ytsearch%d:"%s"' % (num_results, unidecode(textToSearch))
        cmd = [YT_DLP_CMD, "-j", "--no-playlist", "--flat-playlist", yt_search]
        logging.debug("Youtube-dl search command: " + " ".join(cmd))
        try:
            output = subprocess.check_output(cmd).decode("utf-8", "ignore")
            logging.debug("Search results: " + output)
            rc = []
            for each in output.split("\n"):
                if len(each) > 2:
                    j = json.loads(each)
                    if (not "title" in j) or (not "url" in j):
                        continue
                    rc.append([j["title"], j["url"], j["id"]])
            return rc
        except Exception as e:
            logging.debug("Error while executing search: " + str(e))
            raise e

    def get_karaoke_search_results(self, songTitle):
        return self.get_search_results(songTitle + " karaoke")

    def download_video(self, video_url: str, enqueue=False, user="Pikaraoke"):
        logging.info(f"Downloading video: {video_url}")

        yt_id = self.get_youtube_id_from_url(video_url)
        found_song = self.find_song_by_youtube_id(yt_id)

        if found_song:
            logger.info(f"Already downloaded the song `{Path(found_song).name}`")
            if enqueue:
                self.enqueue(found_song, user)

            return 0

        dl_path = self.download_path.joinpath("%(title)s---%(id)s.%(ext)s")
        file_quality = (
            "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
            if self.high_quality
            else "mp4"
        )
        cmd = [YT_DLP_CMD, "-f", file_quality, "-o", str(dl_path), video_url]
        logging.debug("Youtube-dl command: " + " ".join(cmd))
        result = subprocess.call(cmd)
        if result != 0:
            logging.error("Error code while downloading, retrying once...")
            result = subprocess.call(cmd)  # retry once. Seems like this can be flaky
        if result == 0:
            logging.debug("Song successfully downloaded: " + video_url)
            self.get_available_songs()
            if enqueue:
                if found_song:
                    self.enqueue(found_song, user)
                else:
                    logging.error("queueing song: " + video_url)
        else:
            logging.error("Error downloading song: " + video_url)

        return result

    def get_available_songs(self):
        logging.info(f"Fetching available songs in: {self.download_path}")
        types = [".mp4", ".mp3", ".zip", ".mkv", ".avi", ".webm", ".mov"]
        files_grabbed: list[Path] = []

        for file in self.download_path.rglob("*.*"):
            if file.suffix.lower() in types and file.is_file():
                logging.debug("adding song: " + file.name)
                files_grabbed.append(file)

        sorted_files = sorted(files_grabbed, key=lambda f: f.name.lower())
        self.available_songs = list(map(str, sorted_files))
        logger.debug(f"{self.available_songs=}")

    def delete(self, song_path: Path):
        logging.info(f"Deleting song: {song_path}")
        song_path.unlink(missing_ok=True)

        cdg_file = song_path.with_suffix(".cdg")
        cdg_file.unlink(missing_ok=True)

        self.get_available_songs()

    def rename(self, song_path: str, new_name: str):
        logging.info("Renaming song: '" + song_path + "' to: " + new_name)
        base, ext = os.path.splitext(song_path)
        new_file_name = new_name + ext
        os.rename(song_path, str(self.download_path) + new_file_name)

        # if we have an associated cdg file, rename that too
        cdg_file = song_path.replace(ext, ".cdg")
        if os.path.exists(cdg_file):
            os.rename(cdg_file, str(self.download_path) + new_name + ".cdg")
        self.get_available_songs()

    def filename_from_path(self, file_path: str) -> str:
        return Path(file_path).stem.split("---")[0]  # removes youtube id if present

    def find_song_by_youtube_id(self, youtube_id):
        for song in self.available_songs:
            if youtube_id in song:
                return song
        logging.error(f"No available song found with {youtube_id=}")
        return None

    def get_youtube_id_from_url(self, url: str):
        s = url.split("watch?v=")
        if len(s) == 2:
            return s[1]
        else:
            logging.error("Error parsing youtube id from url: " + url)
            return None

    def play_file(self, file: str, semitones: int = 0):
        logging.info(f"Playing file: {file} transposed {semitones} semitones")
        stream_uid = int(time.time())
        stream_url = f"{self.ffmpeg_url}/{stream_uid}"
        # pass a 0.0.0.0 IP to ffmpeg which will work for both hostnames and direct IP access
        ffmpeg_url = f"http://0.0.0.0:{self.ffmpeg_port}/{stream_uid}"

        pitch = 2 ** (
            semitones / 12
        )  # The pitch value is (2^x/12), where x represents the number of semitones

        try:
            fr = FileResolver(file)
        except Exception as e:
            logging.error("Error resolving file: " + str(e))
            self.queue.pop(0)
            return False

        # use h/w acceleration on pi
        default_vcodec = "h264_v4l2m2m" if self.platform == "raspberry_pi" else "libx264"
        # just copy the video stream if it's an mp4 or webm file, since they are supported natively in html5
        # otherwise use the default h264 codec
        vcodec = (
            "copy"
            if fr.file_extension == ".mp4" or fr.file_extension == ".webm"
            else default_vcodec
        )
        vbitrate = "5M"  # seems to yield best results w/ h264_v4l2m2m on pi, recommended for 720p.

        # copy the audio stream if no transposition, otherwise use the aac codec
        is_transposed = semitones != 0
        acodec = "aac" if is_transposed else "copy"
        input = ffmpeg.input(fr.file_path)
        audio = input.audio.filter("rubberband", pitch=pitch) if is_transposed else input.audio

        if fr.cdg_file_path != None:  # handle CDG files
            logging.info("Playing CDG/MP3 file: " + file)
            # copyts helps with sync issues, fps=25 prevents ffmpeg from needlessly encoding cdg at 300fps
            cdg_input = ffmpeg.input(fr.cdg_file_path, copyts=None)
            video = cdg_input.video.filter("fps", fps=25)
            # cdg is very fussy about these flags. pi needs to encode to aac and cant just copy the mp3 stream
            output = ffmpeg.output(
                audio,
                video,
                ffmpeg_url,
                vcodec=vcodec,
                acodec="aac",
                pix_fmt="yuv420p",
                listen=1,
                f="mp4",
                video_bitrate=vbitrate,
                movflags="frag_keyframe+default_base_moof",
            )
        else:
            video = input.video
            output = ffmpeg.output(
                audio,
                video,
                ffmpeg_url,
                vcodec=vcodec,
                acodec=acodec,
                listen=1,
                f="mp4",
                video_bitrate=vbitrate,
                movflags="frag_keyframe+default_base_moof",
            )

        args = output.get_args()
        logging.debug(f"COMMAND: ffmpeg {args}")

        self.kill_ffmpeg()

        self.ffmpeg_process = output.run_async(pipe_stderr=True, pipe_stdin=True)

        # ffmpeg outputs everything useful to stderr for some insane reason!
        # prevent reading stderr from being a blocking action
        q = Queue()
        t = Thread(target=enqueue_output, args=(self.ffmpeg_process.stderr, q))
        t.daemon = True
        t.start()

        while self.ffmpeg_process.poll() is None:
            try:
                output = q.get_nowait()
                logging.debug("[FFMPEG] " + decode_ignore(output))
            except Empty:
                pass
            else:
                if "Stream #" in decode_ignore(output):
                    logging.debug("Stream ready!")
                    # Ffmpeg outputs "Stream #0" when the stream is ready to consume
                    self.now_playing = self.filename_from_path(file)
                    self.now_playing_filename = file
                    self.now_playing_transpose = semitones
                    self.now_playing_url = stream_url
                    self.now_playing_user = self.queue[0]["user"]
                    self.is_paused = False
                    self.queue.pop(0)

                    # Keep logging output until the splash screen reports back that the stream is playing
                    max_retries = 100
                    while self.is_playing == False and max_retries > 0:
                        time.sleep(0.1)  # prevents loop from trying to replay track
                        try:
                            output = q.get_nowait()
                            logging.debug("[FFMPEG] " + decode_ignore(output))
                        except Empty:
                            pass
                        max_retries -= 1
                    if self.is_playing:
                        logging.debug("Stream is playing")
                        break
                    else:
                        logging.error(
                            "Stream was not playable! Run with debug logging to see output. Skipping track"
                        )
                        self.end_song()
                        break

    def kill_ffmpeg(self):
        logging.debug("Killing ffmpeg process")
        if self.ffmpeg_process:
            self.ffmpeg_process.kill()

    def start_song(self):
        logging.info(f"Song starting: {self.now_playing}")
        self.is_playing = True

    def end_song(self):
        logging.info(f"Song ending: {self.now_playing}")
        self.reset_now_playing()
        self.kill_ffmpeg()
        logging.debug("ffmpeg process killed")

    def transpose_current(self, semitones):
        logging.info(f"Transposing current song {self.now_playing} by {semitones} semitones")
        # Insert the same song at the top of the queue with transposition
        self.enqueue(self.now_playing_filename, self.now_playing_user, semitones, True)
        self.skip()

    def is_file_playing(self):
        return self.is_playing

    def is_song_in_queue(self, song_path: str):
        return any(song.get("file", "") == song_path for song in self.queue)

    def enqueue(
        self,
        song_path: str,
        user: str = "Pikaraoke",
        semitones: int = 0,
        add_to_front: bool = False,
    ):
        if self.is_song_in_queue(song_path):
            logging.warning(f"Song is already in queue, will not add: {song_path=}")
            return False
        else:
            queue_item = {
                "user": user,
                "file": song_path,
                "title": self.filename_from_path(song_path),
                "semitones": semitones,
            }
            logger.debug(f"Creating {queue_item=}")

            if add_to_front:
                logging.info(f"'{user}' is adding song to front of queue: {song_path}")
                self.queue.insert(0, queue_item)
            else:
                logging.info(f"'{user}' is adding song to queue: {song_path}")
                self.queue.append(queue_item)

            return True

    def queue_add_random(self, amount):
        logging.info("Adding %d random songs to queue" % amount)
        songs = list(self.available_songs)  # make a copy
        if len(songs) == 0:
            logging.warn("No available songs!")
            return False
        i = 0
        while i < amount:
            r = random.randint(0, len(songs) - 1)
            if self.is_song_in_queue(songs[r]):
                logging.warn("Song already in queue, trying another... " + songs[r])
            else:
                self.enqueue(songs[r], "Randomizer")
                i += 1
            songs.pop(r)
            if len(songs) == 0:
                logging.warn("Ran out of songs!")
                return False
        return True

    def queue_clear(self):
        logging.info("Clearing queue!")
        self.queue.clear()
        self.skip()

    def queue_edit(self, song_name, action):
        index = 0
        song = None
        for each in self.queue:
            if song_name in each["file"]:
                song = each
                break
            else:
                index += 1
        if song == None:
            logging.error("Song not found in queue: " + song["file"])
            return False
        if action == "up":
            if index < 1:
                logging.warn("Song is up next, can't bump up in queue: " + song["file"])
                return False
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                return True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warn("Song is already last, can't bump down in queue: " + song["file"])
                return False
            else:
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
                return True
        elif action == "delete":
            logging.info("Deleting song from queue: " + song["file"])
            del self.queue[index]
            return True
        else:
            logging.error("Unrecognized direction: " + action)
            return False

    def skip(self):
        if self.is_file_playing():
            logging.info("Skipping: " + self.now_playing)
            self.now_playing_command = "skip"
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self):
        if self.is_file_playing():
            logging.info("Toggling pause: " + self.now_playing)
            self.now_playing_command = "pause"
            self.is_paused = not self.is_paused
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def volume_change(self, vol_level):
        self.volume = vol_level
        logging.debug(f"Setting volume to: {self.volume}")
        if self.is_file_playing():
            self.now_playing_command = f"volume_change: {self.volume}"
        return True

    def vol_up(self):
        self.volume += 0.1
        logging.debug(f"Increasing volume by 10%: {self.volume}")
        if self.is_file_playing():
            self.now_playing_command = "vol_up"
            return True
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            return False

    def vol_down(self):
        self.volume -= 0.1
        logging.debug(f"Decreasing volume by 10%: {self.volume}")
        if self.is_file_playing():
            self.now_playing_command = "vol_down"
            return True
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            return False

    def restart(self):
        if self.is_file_playing():
            self.now_playing_command = "restart"
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self):
        self.running = False

    def handle_run_loop(self):
        time.sleep(self.loop_interval / 1000)

    def reset_now_playing(self):
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.now_playing_url = None
        self.is_paused = True
        self.is_playing = False
        self.now_playing_transpose = 0

    def run(self):
        logging.info("Starting PiKaraoke!")
        logging.info(f"Connect the player host to: {self.url}/splash")
        self.running = True
        while self.running:
            try:
                if not self.is_file_playing() and self.now_playing != None:
                    self.reset_now_playing()
                if len(self.queue) > 0:
                    if not self.is_file_playing():
                        self.reset_now_playing()
                        i = 0
                        while i < (self.splash_delay * 1000):
                            self.handle_run_loop()
                            i += self.loop_interval
                        self.play_file(
                            file=self.queue[0]["file"],
                            semitones=self.queue[0]["semitones"],
                        )
                self.handle_run_loop()
            except KeyboardInterrupt:
                logging.warn("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        logger.debug(f"Removing '{self.qr_code_path.name}' from '{self.qr_code_path}'")
        self.qr_code_path.unlink(missing_ok=True)

        logger.debug(f"Removing tmp dir '{self._tmp_dir.name}' from '{self._tmp_dir}'")
        self._tmp_dir.rmdir()
