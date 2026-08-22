"""Secure session token management for admin authentication."""

import logging
import secrets
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages secure session tokens with automatic expiration."""

    def __init__(self, session_duration_hours: int = 24):
        """Initialize session manager.

        Args:
            session_duration_hours: How long before a session token expires.
        """
        self.session_duration_hours = session_duration_hours
        self.sessions = {}
        self.lock = threading.Lock()
        self._cleanup_thread = None

    def create_session(self) -> str:
        """Create a new secure session token.

        Returns:
            A cryptographically secure session token (256-bit entropy).
        """
        token = secrets.token_urlsafe(32)
        expiry = time.time() + (self.session_duration_hours * 3600)

        with self.lock:
            self.sessions[token] = expiry

        logger.info(f"Session created, expires in {self.session_duration_hours} hours")
        return token

    def validate_session(self, token: Optional[str]) -> bool:
        """Validate a session token.

        Args:
            token: The session token to validate.

        Returns:
            True if token is valid and not expired, False otherwise.
        """
        if not token:
            return False

        with self.lock:
            if token not in self.sessions:
                return False

            expiry = self.sessions[token]
            if time.time() > expiry:
                del self.sessions[token]
                return False

            return True

    def revoke_session(self, token: Optional[str]) -> None:
        """Revoke a session token.

        Args:
            token: The session token to revoke.
        """
        if not token:
            return

        with self.lock:
            self.sessions.pop(token, None)

        logger.info("Session revoked")

    def start_cleanup(self) -> None:
        """Start background thread to clean up expired sessions."""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._cleanup_thread = threading.Thread(target=self._cleanup_expired, daemon=True)
            self._cleanup_thread.start()
            logger.debug("Session cleanup thread started")

    def _cleanup_expired(self) -> None:
        """Periodically remove expired session tokens."""
        while True:
            time.sleep(300)
            now = time.time()

            with self.lock:
                expired = [token for token, expiry in self.sessions.items() if now > expiry]
                for token in expired:
                    del self.sessions[token]

                if expired:
                    logger.debug(f"Cleaned up {len(expired)} expired sessions")
