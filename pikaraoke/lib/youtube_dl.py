from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from pikaraoke.lib.get_platform import get_installed_js_runtime

yt_dlp_cmd = [sys.executable, "-m", "yt_dlp"]

# YouTube video IDs are always exactly 11 characters
YOUTUBE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")


@dataclass
class SearchResult:
    """One YouTube search hit. Channel and duration may be empty."""

    title: str
    url: str
    video_id: str
    channel: str
    duration: str  # formatted M:SS, not seconds


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

    The ID is validated against the canonical 11-character shape so tracking
    params (&pp=, &t=, &si=) can never leak into the ID and silently break
    lookups of the downloaded file.

    Args:
        url: YouTube video URL.

    Returns:
        The video ID string, or None if parsing failed.
    """
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("youtu.be"):
        video_id = parsed.path.lstrip("/").split("/")[0]
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]

    if not YOUTUBE_ID_PATTERN.fullmatch(video_id):
        logging.error(f"Error parsing youtube id from url: {url}")
        return None
    return video_id


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
        logging.debug(output)
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
        logging.error(f"Failed to upgrade yt-dlp. Current version: {youtubedl_version}")
    return youtubedl_version


# Prefixes on the lines the templates below produce, read back by DownloadManager.
PROGRESS_PREFIX = "[pk]|"
POSTPROCESS_PREFIX = "[pk-post]|"
SIZE_PREFIX = "[pk-size]|"

# Pipe-delimited: speed and ETA render as "Unknown B/s" when yt-dlp has no estimate, so
# whitespace splitting breaks exactly when the download is struggling.
_DOWNLOAD_PROGRESS_TEMPLATE = (
    f"download:{PROGRESS_PREFIX}%(progress._percent_str)s|%(progress._speed_str)s"
    "|%(progress._eta_str)s|%(info.vcodec)s"
)
_POSTPROCESS_PROGRESS_TEMPLATE = f"postprocess:{POSTPROCESS_PREFIX}%(progress.postprocessor)s"
# Both formats' sizes, emitted once before the first byte.
_SIZE_PRINT_TEMPLATE = f"before_dl:{SIZE_PREFIX}%(requested_formats.:.filesize,filesize_approx)s"


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
        high_quality: If True, cap the download at 1080p; otherwise cap it at 720p.
        youtubedl_proxy: Optional proxy server URL.
        additional_args: Optional additional command-line arguments as a string.

    Returns:
        List of command-line arguments for subprocess execution.
    """
    dl_path = os.path.join(download_path, "%(title)s---%(id)s.%(ext)s")
    # AV1 ships in an mp4 container, so ext!=webm admits it and -S only sorts. Filtering
    # on the codec is what keeps playback a stream copy instead of a libx264 transcode.
    # Both branches must be DASH: "mp4" selects the best pre-merged format, and YouTube
    # refuses those far more often than it refuses the separate streams.
    height = 1080 if high_quality else 720
    # The capped fallback matters because -S sorts on resolution: without it, a video with
    # no avc1 DASH pair lands on a 1080p HLS variant no matter what the cap says.
    file_quality = (
        f"bestvideo[vcodec^=avc1][height<={height}]+bestaudio[ext!=webm]"
        f"/best[ext!=webm][height<={height}]"
        "/best[ext!=webm]"
    )
    args = [
        "-f",
        file_quality,
        "--newline",
        "--progress-template",
        _DOWNLOAD_PROGRESS_TEMPLATE,
        "--progress-template",
        _POSTPROCESS_PROGRESS_TEMPLATE,
        "--print",
        _SIZE_PRINT_TEMPLATE,
        # --print implies --quiet, which would silence the progress lines above.
        "--no-quiet",
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


def get_search_results(query: str) -> list[SearchResult]:
    """Search YouTube for videos matching the query.

    Args:
        query: Search query string.

    Returns:
        One SearchResult per hit, in the order yt-dlp reported them.
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
            results.append(SearchResult(j["title"], j["url"], j["id"], channel, duration_str))
        return results
    except subprocess.CalledProcessError as e:
        logging.debug(f"Error while executing search: {e}")
        raise


# yt-dlp reports HLS variants as ext=mp4 even though their URL is an .m3u8 playlist
# that only Safari can play, so protocol=https is what keeps the preview progressive.
PREVIEW_FORMAT = (
    "worst[ext=mp4][protocol=https][vcodec!=none][acodec!=none]"
    "/worst[protocol=https][vcodec!=none][acodec!=none]"
)

# The SABR rollout is session-dependent, so even a good client is occasionally refused,
# and a <video> element gets one attempt at a bad URL.
_PREVIEW_ATTEMPTS = 3
_VERIFY_TIMEOUT_SECONDS = 5


def _resolve_stream_url(video_url: str) -> str | None:
    """Ask yt-dlp for a progressive stream URL. No guarantee it will serve bytes."""
    # YouTube refuses the stream URLs yt-dlp's default client resolves, but serves android's.
    cmd = yt_dlp_cmd + [
        "-g",
        "-f",
        PREVIEW_FORMAT,
        "--extractor-args",
        "youtube:player_client=android",
    ]
    cmd += _js_runtime_args()
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
        stream_url = output.splitlines()[0]
        # Available formats vary between calls, so a manifest can still slip through.
        if ".m3u8" in stream_url or "/manifest/" in stream_url:
            logging.warning(f"yt-dlp returned a manifest, not a stream, for: {video_url}")
            return None
        return stream_url
    except subprocess.TimeoutExpired:
        logging.error(f"yt-dlp stream URL timed out for: {video_url}")
        return None
    except (FileNotFoundError, PermissionError) as e:
        logging.error(f"Could not run yt-dlp: {e}")
        return None


def _serves_bytes(stream_url: str) -> bool:
    """Check the stream answers a browser, asking for one block so nothing downloads."""
    # Lazy so importing this module never pulls requests in ahead of gevent's patching.
    import requests

    try:
        response = requests.get(
            stream_url,
            headers={"Range": "bytes=0-1023"},
            timeout=_VERIFY_TIMEOUT_SECONDS,
            stream=True,
        )
        response.close()
        return response.status_code in (200, 206)
    except requests.RequestException as e:
        logging.warning(f"Preview stream check failed: {e}")
        return False


def get_stream_url(video_url: str) -> str | None:
    """Get a browser-playable stream URL for a YouTube video, without downloading it.

    Args:
        video_url: YouTube video URL.

    Returns:
        A stream URL confirmed to serve bytes, or None if every attempt was refused.
    """
    for attempt in range(_PREVIEW_ATTEMPTS):
        stream_url = _resolve_stream_url(video_url)
        if stream_url is None:
            return None
        if _serves_bytes(stream_url):
            return stream_url
        logging.info(
            f"Preview stream refused for {video_url}, re-resolving "
            f"(attempt {attempt + 1}/{_PREVIEW_ATTEMPTS})"
        )
    return None
