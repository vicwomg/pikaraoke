"""API rate limiting for security."""

import logging

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)


def init_rate_limiter(app):
    """Initialize Flask-Limiter for the app.

    Args:
        app: Flask application instance.

    Returns:
        Configured Limiter instance.
    """
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",
    )

    logger.info("Rate limiter initialized with per-IP limiting")
    return limiter
