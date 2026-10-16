"""Per-caller brute-force throttle for the login doors.

In-memory and per-process on purpose: an attacker cannot restart the box to clear
it, and a host restart clearing it is the intended reset. Keyed by an opaque caller
string, so the admin password and, later, a singer's share one mechanism.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_FREE_ATTEMPTS = 5
_BASE_COOLDOWN = 30.0
_MAX_COOLDOWN = 15 * 60.0


@dataclass
class _Caller:
    failures: int = 0
    locked_until: float = 0.0


class LoginThrottle:
    """Counts failed logins per caller and locks one out for a doubling cooldown."""

    def __init__(
        self,
        free_attempts: int = _FREE_ATTEMPTS,
        base_cooldown: float = _BASE_COOLDOWN,
        max_cooldown: float = _MAX_COOLDOWN,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._free = free_attempts
        self._base = base_cooldown
        self._max = max_cooldown
        self._clock = clock
        self._callers: dict[str, _Caller] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float | None:
        """Seconds the caller must wait, or None if free now.

        Consulted before verifying, so a locked-out flood never reaches the slow hash.
        """
        with self._lock:
            caller = self._callers.get(key)
            if caller is None:
                return None
            remaining = caller.locked_until - self._clock()
            return remaining if remaining > 0 else None

    def record_failure(self, key: str) -> float | None:
        """Count a wrong password; return the cooldown it triggered, or None."""
        with self._lock:
            caller = self._callers.setdefault(key, _Caller())
            caller.failures += 1
            if caller.failures <= self._free:
                return None
            cooldown = min(self._base * 2 ** (caller.failures - self._free - 1), self._max)
            caller.locked_until = self._clock() + cooldown
            return cooldown

    def record_success(self, key: str) -> None:
        """Clear a caller's history once they prove the password."""
        with self._lock:
            self._callers.pop(key, None)
