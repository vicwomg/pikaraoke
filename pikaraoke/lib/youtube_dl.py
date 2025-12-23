"""YouTube download utilities using yt-dlp."""

import logging
import shlex
import subprocess

from pikaraoke.lib.get_platform import get_installed_js_runtime


def get_youtubedl_version(youtubedl_path: str) -> str:
    """Get the installed yt-dlp version.

    Args:
        youtubedl_path: Path to the yt-dlp executable.

    Returns:
        Version string of the installed yt-dlp.
    """
    return subprocess.check_output([youtubedl_path, "--version"]).strip().decode("utf8")


def get_youtube_id_from_url(url: str) -> str | None:
    """Extract the YouTube video ID from a URL.

    Supports youtube.com/watch?v=, m.youtube.com/?v=, and youtu.be/ formats.

    Args:
        url: YouTube video URL.

    Returns:
        The video ID string, or None if parsing failed.
    """
    if "v=" in url:  # accommodates youtube.com/watch?v= and m.youtube.com/?v=
        s = url.split("watch?v=")
    else:  # accommodates youtu.be/
        s = url.split("u.be/")
    if len(s) == 2:
        if "?" in s[1]:  # Strip unneeded YouTube params
            s[1] = s[1][0 : s[1].index("?")]
        return s[1]
    else:
        logging.error("Error parsing youtube id from url: " + url)
        return None


def upgrade_youtubedl(youtubedl_path: str) -> str:
    """Upgrade yt-dlp to the latest version.

    Attempts self-upgrade first, then falls back to pip if needed.

    Args:
        youtubedl_path: Path to the yt-dlp executable.

    Returns:
        The new version string after upgrade.
    """
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
    youtubedl_version = get_youtubedl_version(youtubedl_path)

    return youtubedl_version


def build_ytdl_download_command(
    youtubedl_path: str,
    video_url: str,
    download_path: str,
    high_quality: bool = False,
    youtubedl_proxy: str | None = None,
    additional_args: str | None = None,
) -> list[str]:
    """Build the yt-dlp command line for downloading a video.

    Args:
        youtubedl_path: Path to the yt-dlp executable.
        video_url: URL of the video to download.
        download_path: Directory path where videos will be saved.
        high_quality: If True, download up to 1080p; otherwise download mp4.
        youtubedl_proxy: Optional proxy server URL.
        additional_args: Optional additional command-line arguments as a string.

    Returns:
        List of command-line arguments for subprocess execution.
    """
    dl_path = download_path + "%(title)s---%(id)s.%(ext)s"
    file_quality = (
        "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
        if high_quality
        else "mp4"
    )
    cmd = [youtubedl_path, "-f", file_quality, "-o", dl_path, "-S", "vcodec:h264"]
    preferred_js_runtime = get_installed_js_runtime()
    if preferred_js_runtime and preferred_js_runtime != "deno":
        # Deno is automatically assumed by yt-dlp, and does not need specification here
        cmd += ["--js-runtimes", preferred_js_runtime]
    if youtubedl_proxy:
        cmd += ["--proxy", youtubedl_proxy]
    if additional_args:
        cmd += shlex.split(additional_args)
    cmd += [video_url]
    return cmd
