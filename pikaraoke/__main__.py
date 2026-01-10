"""Entry point for running PiKaraoke as a module.

This file allows PiKaraoke to be run with: python -m pikaraoke
It's also used by Briefcase for the application entry point.
"""

from pikaraoke.app import main

if __name__ == "__main__":
    main()
