import logging
import shlex
import subprocess


def get_youtubedl_version(youtubedl_path):
    return subprocess.check_output([youtubedl_path, "--version"]).strip().decode("utf8")


def get_youtube_id_from_url(url):
    if "v=" in url:  # accomodates youtube.com/watch?v= and m.youtube.com/?v=
        s = url.split("watch?v=")
    else:  # accomodates youtu.be/
        s = url.split("u.be/")
    if len(s) == 2:
        if "?" in s[1]:  # Strip uneeded Youtube Params
            s[1] = s[1][0 : s[1].index("?")]
        return s[1]
    else:
        logging.error("Error parsing youtube id from url: " + url)
        return None


def upgrade_youtubedl(youtubedl_path):
    try:
        output = (
            subprocess.check_output([youtubedl_path, "-U"], stderr=subprocess.STDOUT)
            .decode("utf8")
            .strip()
        )
    except subprocess.CalledProcessError as e:
        output = e.output.decode("utf8")
    logging.info(output)
    if "You installed yt-dlp with pip or using the wheel from PyPi" in output:
        # allow pip to break system packages (probably required if installed without venv)
        args = ["install", "--upgrade", "yt-dlp", "--break-system-packages"]
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
    youtubedl_version = get_youtubedl_version(youtubedl_path)

    return youtubedl_version


def build_ytdl_download_command(
    youtubedl_path,
    video_url,
    download_path,
    high_quality=False,
    youtubedl_proxy=None,
    additional_args=None,
):
    dl_path = download_path + "%(title)s---%(id)s.%(ext)s"
    file_quality = (
        "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
        if high_quality
        else "mp4"
    )
    cmd = [youtubedl_path, "-f", file_quality, "-o", dl_path, "-S", "vcodec:h264"]
    if youtubedl_proxy:
        cmd += ["--proxy", youtubedl_proxy]
    if additional_args:
        cmd += shlex.split(additional_args)
    cmd += [video_url]
    return cmd
