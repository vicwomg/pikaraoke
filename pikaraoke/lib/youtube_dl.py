from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys

from pikaraoke.lib.get_platform import get_installed_js_runtime

yt_dlp_cmd = [sys.executable, "-m", "yt_dlp"]


def _js_runtime_args() -> list[str]:
    """Return yt-dlp args to select the preferred JS runtime, if any."""
    runtime = get_installed_js_runtime()
    if runtime and runtime != "deno":
        # Deno is automatically assumed by yt-dlp, and does not need specification here
        return ["--js-runtimes", runtime]
    return []


def get_youtubedl_version() -> str:
    """Get the installed yt-dlp version.

    Returns:
        Version string of the installed yt-dlp or an error message.
    """
    try:
        cmd = yt_dlp_cmd + ["--version"]
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
        logging.error(f"Error parsing youtube id from url: {url}")
        return None


def upgrade_youtubedl() -> str:
    """Upgrade yt-dlp to the latest version.

    Attempts self-upgrade first, then falls back to pip if needed.

    Returns:
        The new version string after upgrade.
    """
    try:
        output = (
            subprocess.check_output(yt_dlp_cmd + ["-U"], stderr=subprocess.STDOUT)
            .decode("utf8")
            .strip()
        )
    except subprocess.CalledProcessError as e:
        output = e.output.decode("utf8")
    except (FileNotFoundError, PermissionError) as e:
        logging.warning(f"Could not run yt-dlp for upgrade: {e}")
        return get_youtubedl_version()

    # Check if already up to date
    if "is up to date" in output.lower():
        logging.debug("yt-dlp is already up to date")
        return get_youtubedl_version()

    upgrade_success = False
    if "pip" in output.lower():
        pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]

        # Outside a venv, pip requires --break-system-packages on modern Python
        if sys.prefix == sys.base_prefix:
            pip_cmd.append("--break-system-packages")

        try:
            logging.info(f"yt-dlp is outdated! Attempting upgrade via {pip_cmd}...")
            subprocess.check_output(pip_cmd, stderr=subprocess.STDOUT)
            upgrade_success = True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logging.error(f"Failed to upgrade yt-dlp using pip: {e}")

    youtubedl_version = get_youtubedl_version()
    if upgrade_success:
        logging.info(f"Done. Installed version: {youtubedl_version}")
    else:
        logging.error("Failed to upgrade yt-dlp.")
    return youtubedl_version


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
    dl_path = os.path.join(download_path, "%(title)s---%(id)s.%(ext)s")
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
    cmd = yt_dlp_cmd + args + _js_runtime_args()
    if youtubedl_proxy:
        cmd += ["--proxy", youtubedl_proxy]
    if additional_args:
        cmd += shlex.split(additional_args)
    cmd += [video_url]
    return cmd


def get_search_results(query: str) -> list[list[str]]:
    """Search YouTube for videos matching the query.

    Args:
        query: Search query string.

    Returns:
        List of [title, url, video_id, channel, duration] for each result.
        Duration is formatted as M:SS; channel and duration may be empty strings.
    """
    logging.info(f"Searching YouTube for: {query}")
    num_results = 10
    yt_search = f'ytsearch{num_results}:"{query}"'
    cmd = yt_dlp_cmd + ["-j", "--no-playlist", "--flat-playlist", yt_search]
    logging.debug(f"yt-dlp search command: {' '.join(cmd)}")
    try:
        output = subprocess.check_output(cmd).decode("utf-8", "ignore")
        logging.debug(f"Search results: {output}")
        results = []
        for line in output.split("\n"):
            if len(line) <= 2:
                continue
            j = json.loads(line)
            if "title" not in j or "url" not in j:
                continue
            channel = j.get("channel") or j.get("uploader") or ""
            duration_raw = j.get("duration")
            duration_str = ""
            if isinstance(duration_raw, (int, float)):
                seconds = int(duration_raw)
                duration_str = f"{seconds // 60}:{seconds % 60:02d}"
            results.append([j["title"], j["url"], j["id"], channel, duration_str])
        return results
    except subprocess.CalledProcessError as e:
        logging.debug(f"Error while executing search: {e}")
        raise


def get_stream_url(video_url: str) -> str | None:
    """Get a direct stream URL for a YouTube video without downloading it.

    Args:
        video_url: YouTube video URL.

    Returns:
        Direct playable stream URL, or None if yt-dlp failed.
    """
    cmd = yt_dlp_cmd + ["-g", "-f", "worst[ext=mp4]/worst"] + _js_runtime_args()
    cmd += [video_url]
    logging.debug(f"yt-dlp get stream URL command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0:
            logging.warning(
                f"yt-dlp stream URL failed for {video_url}: {result.stderr.decode('utf-8', 'ignore')}"
            )
            return None
        output = result.stdout.decode("utf-8").strip()
        if not output:
            logging.warning(f"yt-dlp returned empty output for: {video_url}")
            return None
        return output.splitlines()[0]
    except subprocess.TimeoutExpired:
        logging.error(f"yt-dlp stream URL timed out for: {video_url}")
        return None
    except (FileNotFoundError, PermissionError) as e:
        logging.error(f"Could not run yt-dlp: {e}")
        return None
