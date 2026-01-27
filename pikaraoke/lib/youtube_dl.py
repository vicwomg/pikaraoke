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

    Args:
        youtubedl_path: Path to the yt-dlp executable.

    Returns:
        The new version string after upgrade.
    """
    resolved_path = resolve_youtubedl_path(youtubedl_path)
    current_version = get_youtubedl_version(youtubedl_path)
    logging.info(f"Checking for yt-dlp updates... Current version: {current_version}")

    try:
        # 1. Try native self-upgrade first
        logging.debug(f"Attempting native yt-dlp upgrade: {resolved_path} -U")
        output = subprocess.check_output([resolved_path, "-U"], stderr=subprocess.STDOUT).decode(
            "utf8"
        )
        if "yt-dlp is up to date" in output:
            logging.debug("yt-dlp is already up to date via native upgrade.")
            return current_version
        logging.info("yt-dlp upgraded successfully via native upgrade.")
        return get_youtubedl_version(youtubedl_path)
    except subprocess.CalledProcessError as e:
        output = e.output.decode("utf8")
        if "yt-dlp is up to date" in output:
            return current_version
        logging.debug(f"Native upgrade failed or not supported: {output.strip()}")
    except Exception as e:
        logging.debug(f"Native upgrade failed: {e}")

    # 2. Identify installation method and upgrade via package manager
    upgrade_methods = []

    # Check pipx
    if shutil.which("pipx"):
        try:
            pipx_list = (
                subprocess.check_output(["pipx", "list"], stderr=subprocess.DEVNULL)
                .decode("utf8")
                .lower()
            )
            if "package yt-dlp" in pipx_list:
                upgrade_methods.append(["pipx", "upgrade", "yt-dlp"])
        except Exception:
            pass

    # Fallback to pip3/pip
    pip_args = ["install", "--upgrade", "yt-dlp[default]", "--break-system-packages"]
    if shutil.which("pip3"):
        upgrade_methods.append(["pip3"] + pip_args)
    if shutil.which("pip"):
        upgrade_methods.append(["pip"] + pip_args)

    for cmd in upgrade_methods:
        try:
            logging.info(f"Attempting yt-dlp upgrade via: {' '.join(cmd)}")
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            new_version = get_youtubedl_version(youtubedl_path)
            logging.info(f"yt-dlp upgraded successfully. New version: {new_version}")
            return new_version
        except Exception as e:
            logging.debug(f"Upgrade via {' '.join(cmd)} failed: {e}")

    logging.error("Failed to upgrade yt-dlp using any available method.")
    return current_version


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
