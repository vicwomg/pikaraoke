"""Acquire and locate a self-updating yt-dlp standalone binary.

yt-dlp is pinned as a Python dependency so a fresh install always works, but
that pin is frozen in uv.lock: an in-app upgrade is reverted on the next
`uv run`. yt-dlp's standalone binaries sidestep the lock entirely -- they live
in the writable data dir and `yt-dlp -U` replaces the file in place -- so an
upgrade sticks on every deployment.

The pinned module stays the fallback: platforms with no self-updating build
(32-bit ARM, Android) and any offline or failed download keep working through
`python -m yt_dlp`.

Integrity is checked against the release's SHA2-256SUMS. This guards against a
corrupt or truncated download; it is not a substitute for GPG signature
verification against a pinned key, which is deliberately out of scope here.
"""

import glob
import hashlib
import logging
import os
import platform
import sys

from pikaraoke.lib.get_platform import (
    get_data_directory,
    is_android,
    is_linux,
    is_macos,
    is_windows,
)

_RELEASE_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"

# Stream the ~30 MB binary in 64 KiB blocks so a slow Pi link never buffers it
# whole in memory.
_CHUNK_SIZE = 1 << 16


def _is_musl() -> bool:
    """Detect a musl libc (Alpine, some Docker images).

    platform.libc_ver() reports ('', '') on musl rather than naming it, so the
    musl loader is the reliable tell.
    """
    return bool(glob.glob("/lib/ld-musl-*") or glob.glob("/usr/lib/ld-musl-*"))


def _asset_name() -> str | None:
    """Return the yt-dlp release asset for this platform, or None.

    None means no self-updating standalone exists here (32-bit ARM ships only a
    non-updating zip; Android's bionic libc runs none of the Linux builds), so
    the caller should stay on the pinned module.
    """
    if is_android():
        return None

    machine = platform.machine().lower()

    if is_windows():
        if machine in ("amd64", "x86_64"):
            return "yt-dlp.exe"
        if machine in ("arm64", "aarch64"):
            return "yt-dlp_arm64.exe"
        if machine in ("x86", "i386", "i686"):
            return "yt-dlp_x86.exe"
        return None

    if is_macos():
        # One universal build covers both Intel and Apple Silicon.
        return "yt-dlp_macos"

    if is_linux():
        musl = _is_musl()
        if machine in ("x86_64", "amd64"):
            return "yt-dlp_musllinux" if musl else "yt-dlp_linux"
        if machine in ("aarch64", "arm64"):
            return "yt-dlp_musllinux_aarch64" if musl else "yt-dlp_linux_aarch64"
        return None

    return None


def binary_path() -> str:
    """Return where the managed yt-dlp binary lives, present or not."""
    name = "yt-dlp.exe" if is_windows() else "yt-dlp"
    return os.path.join(get_data_directory(), name)


# Announce the yt-dlp source once, and again only when it flips (module ->
# binary after a download), never per call.
_last_logged_command: list[str] | None = None


def ytdlp_command() -> list[str]:
    """Return the command to invoke yt-dlp.

    Prefers the managed standalone binary once it is on disk; otherwise falls
    back to the pinned module. Resolved per call so a download that lands after
    startup is picked up without a restart.
    """
    global _last_logged_command
    path = binary_path()
    using_binary = os.path.exists(path)
    command = [path] if using_binary else [sys.executable, "-m", "yt_dlp"]
    if command != _last_logged_command:
        source = "managed yt-dlp binary" if using_binary else "pinned yt-dlp module"
        logging.info(f"Using {source}")
        _last_logged_command = command
    return command


def ensure_ytdlp_binary() -> str | None:
    """Make sure a managed yt-dlp binary is present, downloading it if absent.

    Returns the binary path, or None when this platform has no self-updating
    build or the download could not be verified. Never raises: a failure just
    leaves the caller on the pinned module.
    """
    asset = _asset_name()
    if asset is None:
        return None

    path = binary_path()
    if os.path.exists(path):
        return path

    if _download_and_verify(asset, path):
        return path
    return None


def _expected_hash(sums_text: str, asset: str) -> str | None:
    """Pull the SHA-256 for asset out of a GNU-format SHA2-256SUMS file."""
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset:
            return parts[0].lower()
    return None


def _download_and_verify(asset: str, dest: str) -> bool:
    """Download asset to dest, verifying its published SHA-256 first.

    Writes to a temporary file and only swaps it into place once the checksum
    matches, so a half-written or tampered file never becomes the live binary.
    """
    # Lazy so importing this module never pulls requests in ahead of gevent's
    # monkey-patching (mirrors youtube_dl._serves_bytes).
    import requests

    tmp = dest + ".download"
    try:
        sums = requests.get(f"{_RELEASE_BASE}/SHA2-256SUMS", timeout=30)
        sums.raise_for_status()
        expected = _expected_hash(sums.text, asset)
        if expected is None:
            logging.error(f"yt-dlp SHA2-256SUMS has no entry for {asset}")
            return False

        digest = hashlib.sha256()
        with requests.get(f"{_RELEASE_BASE}/{asset}", stream=True, timeout=(10, 60)) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    fh.write(chunk)
                    digest.update(chunk)

        if digest.hexdigest() != expected:
            logging.error("Downloaded yt-dlp binary failed checksum verification")
            return False

        if not is_windows():
            os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
        logging.info(f"Installed self-updating yt-dlp binary at {dest}")
        return True
    except (requests.RequestException, OSError) as e:
        logging.warning(f"Could not acquire yt-dlp binary, staying on the pinned module: {e}")
        return False
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
