import logging
import os
import shlex
import shutil
import subprocess
import sys

from pikaraoke.lib.get_platform import get_installed_js_runtime


def resolve_youtubedl_path(youtubedl_path: str) -> str:
    """Resolve the definitive path to the yt-dlp executable.

    If the provided path is the default 'yt-dlp' and is not found in the
    system PATH, this looks in the same directory as the current Python
    executable (useful for pipx and virtualenv environments).

    Args:
        youtubedl_path: The configured path to yt-dlp (e.g. 'yt-dlp').

    Returns:
        The resolved path string.
    """
    if youtubedl_path == "yt-dlp":
        # check system path first
        if shutil.which(youtubedl_path):
            logging.debug(f"Found yt-dlp in system path: {youtubedl_path}")
            return youtubedl_path

        # check relative to current python executable (pipx/venv)
        python_bin_dir = os.path.dirname(sys.executable)
        ext = ".exe" if sys.platform.startswith("win") else ""
        bin_path = os.path.join(python_bin_dir, "yt-dlp" + ext)

        if os.path.isfile(bin_path):
            logging.debug(f"Found yt-dlp in local environment: {bin_path}")
            return bin_path

    return youtubedl_path


def get_youtubedl_version(youtubedl_path: str) -> str:
    """Get the installed yt-dlp version.

    Args:
        youtubedl_path: Path to the yt-dlp executable.

    Returns:
        Version string of the installed yt-dlp or an error message.
    """
    try:
        resolved_path = resolve_youtubedl_path(youtubedl_path)
        logging.debug(f"Getting yt-dlp version using command: {resolved_path} --version")
        return subprocess.check_output([resolved_path, "--version"]).strip().decode("utf8")
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


def upgrade_youtubedl(youtubedl_path: str) -> str:
    """Upgrade yt-dlp to the latest version.

    Attempts self-upgrade first, then falls back to pip if needed.

    Args:
        youtubedl_path: Path to the yt-dlp executable.

    Returns:
        The new version string after upgrade.
    """
    resolved_path = resolve_youtubedl_path(youtubedl_path)
    try:
        output = (
            subprocess.check_output([resolved_path, "-U"], stderr=subprocess.STDOUT)
            .decode("utf8")
            .strip()
        )
    except subprocess.CalledProcessError as e:
        output = e.output.decode("utf8")
    except (FileNotFoundError, PermissionError) as e:
        logging.warning(f"Could not run yt-dlp for upgrade: {e}")
        return get_youtubedl_version(youtubedl_path)

    # Check if already up to date
    if "is up to date" in output.lower():
        logging.debug("yt-dlp is already up to date")
        return get_youtubedl_version(youtubedl_path)

    upgrade_success = False
    if "pip" in output.lower():
        # Check if installed via pipx first, as it's a cleaner upgrade path
        if shutil.which("pipx"):
            try:
                pipx_list = (
                    subprocess.check_output(["pipx", "list"], stderr=subprocess.DEVNULL)
                    .decode("utf8")
                    .lower()
                )
                if "package yt-dlp" in pipx_list:
                    logging.info("yt-dlp is outdated! Attempting upgrade via pipx...")
                    subprocess.check_output(["pipx", "upgrade", "yt-dlp"], stderr=subprocess.STDOUT)
                    upgrade_success = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass

        if not upgrade_success:
            # allow pip to break system packages (probably required if installed without venv)
            args = ["install", "--upgrade", "yt-dlp[default]", "--break-system-packages"]
            try:
                logging.info("yt-dlp is outdated! Attempting upgrade via pip3...")
                subprocess.check_output(["pip3"] + args, stderr=subprocess.STDOUT)
                upgrade_success = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    logging.info("yt-dlp is outdated! Attempting upgrade via pip...")
                    subprocess.check_output(["pip"] + args, stderr=subprocess.STDOUT)
                    upgrade_success = True
                except (subprocess.CalledProcessError, FileNotFoundError):
                    logging.error("Failed to upgrade yt-dlp using pip")

    youtubedl_version = get_youtubedl_version(youtubedl_path)
    if upgrade_success:
        logging.info("Done. Installed version: %s" % youtubedl_version)
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
    resolved_path = resolve_youtubedl_path(youtubedl_path)
    cmd = [
        resolved_path,
        "-f",
        file_quality,
        "-o",
        dl_path,
        "-S",
        "vcodec:h264",
        "--compat-options",
        "filename-sanitization",
    ]
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
