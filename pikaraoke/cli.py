"""Console-script entry point.

Dispatches subcommands before importing pikaraoke.app, which parses CLI args and
sets up gevent and Flask at module load - too early for an in-server front door.
"""

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "set-admin-password":
        from pikaraoke.lib.set_admin_password import set_admin_password_command

        sys.exit(set_admin_password_command(sys.argv[2:]))

    from pikaraoke.app import main as run_server

    run_server()
