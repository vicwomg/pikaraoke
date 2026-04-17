"""Dev runner: watches the pikaraoke package and restarts the app on change."""

import sys
from pathlib import Path

import watchfiles

_WATCH_EXTS: frozenset[str] = frozenset({".py", ".html", ".ini"})
_SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".git", "translations"})


def _filter(_change: watchfiles.Change, path: str) -> bool:
    p = Path(path)
    if any(part in _SKIP_DIRS for part in p.parts):
        return False
    return p.suffix in _WATCH_EXTS


def _run_app(forwarded_args: list[str]) -> None:
    sys.argv[1:] = forwarded_args
    from pikaraoke.app import main

    main()


def main() -> None:
    pkg_root = Path(__file__).parent
    forwarded = sys.argv[1:]
    print(f"[pikaraoke-dev] watching {pkg_root} (.py .html .ini); args={forwarded}")
    watchfiles.run_process(
        str(pkg_root),
        target=_run_app,
        args=(forwarded,),
        watch_filter=_filter,
        debounce=800,
    )
