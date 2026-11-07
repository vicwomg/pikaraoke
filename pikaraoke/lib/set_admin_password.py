"""The `pikaraoke set-admin-password` subcommand.

Sets the admin password without a running server, reusing AdminAuth so PBKDF2
and the [SECRETS] format stay in one place. Installers call it as an optional,
skippable setup step; open-by-default stays the default when it is skipped.
"""

import argparse
import getpass
import sys

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.preference_manager import PreferenceManager


def _read_password() -> str | None:
    """Prompt interactively, or read one piped line non-interactively.

    Returns the password, "" to leave the box open, or None to abort.
    """
    if sys.stdin.isatty():
        password = getpass.getpass("Admin password (leave blank to keep it open): ")
        if not password:
            return ""
        if password != getpass.getpass("Confirm password: "):
            print("Passwords did not match; nothing changed.", file=sys.stderr)
            return None
        return password
    return sys.stdin.readline().rstrip("\r\n")


def set_admin_password_command(argv: list[str]) -> int:
    """Run the subcommand. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="pikaraoke set-admin-password",
        description="Set the PiKaraoke admin password without launching the server.",
    )
    parser.add_argument(
        "--config-file-path",
        default="config.ini",
        help="Config file to target (relative paths resolve under the data directory).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Exit 0 if a password is set, 1 if not. Writes nothing.",
    )
    args = parser.parse_args(argv)

    admin_auth = AdminAuth(PreferenceManager(args.config_file_path))

    if args.status:
        return 0 if admin_auth.is_password_set() else 1

    if admin_auth.is_password_set():
        print("An admin password is already set; leaving it unchanged.")
        return 0

    password = _read_password()
    if password is None:
        return 1
    if not password:
        print("No password set. PiKaraoke stays open to everyone on the network.")
        return 0

    admin_auth.set_password(password)
    print("Admin password set.")
    return 0
