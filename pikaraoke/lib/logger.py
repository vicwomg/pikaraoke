import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from pikaraoke import PACKAGE  # Because __package__ will return pikaraoke.lib
from pikaraoke import Platform, get_platform


def get_log_directory() -> Path:
    """Get the log directory path based on the operating system

    Returns:
        Path: The path to the log directory

    Raises:
        OSError: If the operating system is unsupported
    """
    platform: Platform = get_platform()

    if platform.is_unknown():
        raise OSError("Unsupported OS. Can't determine logs folder.")

    user_home = Path.home()
    if platform.is_windows():
        return user_home / "AppData" / "Local" / PACKAGE / "Logs"

    return user_home / ".config" / PACKAGE / "logs"  # macOs and Linux use the same log path


def clean_old_logs(log_dir: Path, max_files: int = 5):
    """Remove old log files, keeping only the most recent `max_files` logs

    This function sorts log files in the specified directory by their modification time
    and removes the oldest files until only `max_files` remain.

    Args:
        log_dir (Path): The directory where the log files are stored.
        max_files (int, optional): The maximum number of log files to keep. Defaults to 5.

    Raises:
        FileNotFoundError: If the specified log directory does not exist.
        PermissionError: If there is no permission to delete log files.

    Example:
        ```python
        from pathlib import Path
        clean_old_logs(Path('/var/log/myapp'), max_files=10)
        ```
    """
    import os

    log_files = sorted(log_dir.glob("*.log"), key=os.path.getmtime)
    while len(log_files) > max_files:
        old_log = log_files.pop(0)
        old_log.unlink()


class CustomFormatter(logging.Formatter):
    def format(self, record):
        record.levelname = record.levelname.ljust(8)  # Adjust the number as needed
        return super().format(record)


def configure_logger(
    log_level: int = logging.DEBUG, log_dir: Path | None = None, max_log_files: int = 5
):
    """Configures the logger with log file, format and level

    Sets up a file to log to, the level to log at and configures a nice format for the log to be
    displayed. There are two formatters, one for the console and one for the log file. The console
    formatter is a bit simpler and removes the date and time as it's quite long and noisy. The
    console typically just wants to get a quick overview. The log file will hold all of the detailed
    information on time and date.

    The log files are stored under logs/ folder and the name is the date and time appended .log

    This configurations also discovers all loggers and configures them the same if there are other
    loggers by third party libraries that have their own configuration.

    Args:
        log_level (int): The log level to log at. logging.[DEBUG | INFO | ERROR | CRITICAL | WARN ].
            Defaults to logging.DEBUG.
        log_dir (Path | None): Where to store the logs. Defaults to system default.
        max_log_files (int): Keeps only the previous (n) number of log files. Defaults to 5.
    """
    if log_dir is None:
        log_dir = get_log_directory()

    clean_old_logs(log_dir=log_dir, max_files=max_log_files)

    # Generate filename with current date and time
    log_filename = log_dir / datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log")
    log_dir.mkdir(exist_ok=True, parents=True)  # Create logs/ folder

    # Create handlers
    # file_handler = logging.FileHandler(log_filename)
    file_handler = logging.handlers.RotatingFileHandler(
        log_filename, maxBytes=10 * 1024**2, backupCount=5
    )
    stream_handler = logging.StreamHandler()

    # Create formatters
    file_formatter = CustomFormatter(
        "[%(asctime)s] %(levelname)s %(message)s", datefmt="%d.%m.%Y %H:%M:%S"
    )
    console_formatter = logging.Formatter("%(levelname)s %(message)s", datefmt="%H:%M:%S")

    # Set formatters to handlers
    file_handler.setFormatter(file_formatter)
    stream_handler.setFormatter(console_formatter)

    # Configure logging
    logging.basicConfig(level=log_level, handlers=[file_handler, stream_handler])

    # Ensure all existing loggers use the same configuration
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        logger.handlers.clear()  # Clear existing handlers
        if isinstance(logger, logging.Logger):
            logger.addHandler(file_handler)
            logger.addHandler(stream_handler)
            logger.setLevel(log_level)
