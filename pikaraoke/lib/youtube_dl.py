import logging
import shlex
import subprocess

from pikaraoke.lib.get_platform import get_installed_js_runtime


class YtDlpClient:
    def __init__(self, youtubedl_path, youtubedl_proxy=None, additional_args=None):
        self.youtubedl_path = youtubedl_path
        self.youtubedl_proxy = youtubedl_proxy
        self.additional_args = additional_args

    def get_version(self):
        return (
            subprocess.check_output([self.youtubedl_path, "--version"])
            .strip()
            .decode("utf8")
        )

    @staticmethod
    def get_youtube_id_from_url(url):
        if "v=" in url:  # accomodates youtube.com/watch?v= and m.youtube.com/?v=
            s = url.split("watch?v=")
        else:  # accomodates youtu.be/
            s = url.split("u.be/")
        if len(s) == 2:
            if "?" in s[1]:  # Strip uneeded Youtube Params
                s[1] = s[1][0: s[1].index("?")]
            return s[1]
        else:
            logging.error("Error parsing youtube id from url: " + url)
            return None

    def upgrade(self):
        try:
            output = (
                subprocess.check_output(
                    [self.youtubedl_path, "-U"], stderr=subprocess.STDOUT
                )
                .decode("utf8")
                .strip()
            )
        except subprocess.CalledProcessError as e:
            output = e.output.decode("utf8")
        logging.info(output)
        if "You installed yt-dlp with pip or using the wheel from PyPi" in output:
            # allow pip to break system packages (probably required if installed without venv)
            args = ["install", "--upgrade", "yt-dlp[default]", "--break-system-packages"]
            try:
                logging.info("Attempting youtube-dl upgrade via pip3...")
                output = (
                    subprocess.check_output(["pip3"] + args, stderr=subprocess.STDOUT)
                    .decode("utf8")
                    .strip()
                )
            except FileNotFoundError:
                logging.info("Attempting youtube-dl upgrade via pip...")
                output = (
                    subprocess.check_output(["pip"] + args, stderr=subprocess.STDOUT)
                    .decode("utf8")
                    .strip()
                )
        return self.get_version()

    def build_download_command(
            self,
            video_url,
            download_path,
            high_quality=False
    ):
        dl_path = download_path + "%(title)s---%(id)s.%(ext)s"
        file_quality = (
            "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
            if high_quality
            else "mp4"
        )
        cmd = [self.youtubedl_path, "-f", file_quality, "-o", dl_path, "-S", "vcodec:h264", ]

        preferred_js_runtime = get_installed_js_runtime()
        if preferred_js_runtime and preferred_js_runtime != "deno":
            # Deno is automatically assumed by yt-dlp, and does not need specification here
            cmd += ["--js-runtimes", preferred_js_runtime]

        proxy = self.youtubedl_proxy
        if proxy:
            cmd += ["--proxy", proxy]

        extra_args = self.additional_args
        if extra_args:
            cmd += shlex.split(extra_args)

        cmd += [video_url]
        return cmd
