import json
import logging
import os
import shlex
import stat
import subprocess
import sys
import urllib.request
from urllib.error import URLError

from pikaraoke.lib.get_platform import (
    get_bin_directory,
    get_installed_js_runtime,
    is_windows,
)


def get_ytdlp_cmd() -> list[str]:
    """Get the command to run yt-dlp.

    Checks for a standalone binary in the pikaraoke bin directory first.
    Falls back to the python module if the binary is missing.

    Returns:
        List of command strings (e.g. ['/path/to/yt-dlp'] or ['python', '-m', 'yt_dlp'])
    """
    bin_dir = get_bin_directory()
    binary_name = "yt-dlp.exe" if is_windows() else "yt-dlp"
    binary_path = os.path.join(bin_dir, binary_name)

    if os.path.exists(binary_path):
        return [binary_path]

    # Fallback to module if binary is not yet downloaded
    return [sys.executable, "-m", "yt_dlp"]


def get_youtubedl_version() -> str:
    """Get the installed yt-dlp version.

    Args:
    Returns:
        Version string of the installed yt-dlp or an error message.
    """
    try:
        cmd = get_ytdlp_cmd() + ["--version"]
        return subprocess.check_output(cmd).strip().decode("utf8")
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as e:
        logging.warning(f"Could not get yt-dlp version: {e}")
        return "Not found"
    except Exception as e:
        logging.error(f"Unexpected error getting yt-dlp version: {e}")
        return "Error"


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


def upgrade_youtubedl() -> str:
    """Upgrade yt-dlp to the latest version.

    Downloads the latest standalone binary release from GitHub.

    Args:
    Returns:
        The new version string after upgrade.
    """
    bin_dir = get_bin_directory()
    binary_name = "yt-dlp.exe" if is_windows() else "yt-dlp"
    binary_path = os.path.join(bin_dir, binary_name)

    # URL selection based on OS
    if is_windows():
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    else:
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"

    logging.info(f"Checking for yt-dlp upgrades (Binary mode)...")
    logging.debug(f"Target path: {binary_path}")

    try:
        # Download the file
        logging.info(f"Downloading latest yt-dlp from {url}...")
        with urllib.request.urlopen(url) as response:
            with open(binary_path, "wb") as f:
                f.write(response.read())

        # Make it executable (Unix only)
        if not is_windows():
            st = os.stat(binary_path)
            os.chmod(binary_path, st.st_mode | stat.S_IEXEC)

        new_version = get_youtubedl_version()
        logging.info(f"Upgrade complete. Installed version: {new_version}")
        return new_version

    except Exception as e:
        logging.error(f"Failed to upgrade yt-dlp binary: {e}")
        return get_youtubedl_version()


def build_ytdl_download_command(
    video_url: str,
    download_path: str,
    high_quality: bool = False,
    youtubedl_proxy: str | None = None,
    additional_args: str | None = None,
) -> list[str]:
    """Build the yt-dlp command line for downloading a video.

    Args:
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
    args = [
        "-f",
        file_quality,
        "-o",
        dl_path,
        "-S",
        "vcodec:h264",
        "--compat-options",
        "filename-sanitization",
    ]

    cmd = get_ytdlp_cmd() + args

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


def get_search_results(textToSearch: str) -> list[list[str]]:
    """Search YouTube for videos matching the query.

    Args:
        textToSearch: Search query string.

    Returns:
        List of [title, url, video_id] for each result.

    Raises:
        Exception: If the search fails.
    """
    logging.info("Searching YouTube for: " + textToSearch)
    num_results = 10
    yt_search = 'ytsearch%d:"%s"' % (num_results, textToSearch)

    cmd = get_ytdlp_cmd() + ["-j", "--no-playlist", "--flat-playlist", yt_search]

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
