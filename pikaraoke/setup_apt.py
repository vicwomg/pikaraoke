"""APT package installation utilities for Debian-based systems."""

import subprocess


def run_command(command: str) -> None:
    """Run a shell command and raise an exception on failure.

    Args:
        command: Shell command string to execute.

    Raises:
        Exception: If the command returns a non-zero exit code.
    """
    result = subprocess.run(command, shell=True, text=True)
    if result.returncode != 0:
        raise Exception(f"ERROR: '{command}' failed with error code: {result.returncode}")


def apt(command: str) -> None:
    """Run an apt-get command with sudo.

    Args:
        command: apt-get subcommand and arguments (e.g., 'install ffmpeg -y').
    """
    run_command(f"sudo apt-get {command}")


def main() -> None:
    """Install required system packages (ffmpeg, chromium) via apt."""
    print("*** INSTALLING REQUIRED BINARIES ***")
    apt("update --allow-releaseinfo-change")
    apt("install ffmpeg -y")
    apt("install chromium-browser -y")
    apt("install chromium-chromedriver -y")


if __name__ == "__main__":
    main()
