import enum
import secrets


class Config:
    """Base configuration."""

    SECRET_KEY = secrets.token_bytes(24)
    BABEL_TRANSLATION_DIRECTORIES = "translations"
    JSON_SORT_KEYS = False
    SITE_NAME = "PiKaraoke"
    ADMIN_PASSWORD = None
    # Add other base settings here


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    # Add production-specific settings here


# Example environment-specific settings
class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True


class ConfigType(enum.Enum):
    DEVELOPMENT = DevelopmentConfig
    PRODUCTION = ProductionConfig
    TESTING = TestingConfig
