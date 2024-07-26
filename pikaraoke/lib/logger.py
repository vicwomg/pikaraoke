import logging

from pathlib import Path
from datetime import datetime
import cherrypy

class CustomFormatter(logging.Formatter):
    def format(self, record):
        record.levelname = record.levelname.ljust(8)  # Adjust the number as needed
        return super().format(record)

def configure_logger(log_level: int = logging.DEBUG):
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
    """
    # Generate filename with current date and time
    logs_folder = Path("logs")
    log_filename = logs_folder / datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log")
    logs_folder.mkdir(exist_ok=True)  # Create logs/ folder

    # Create handlers
    file_handler = logging.FileHandler(log_filename)
    stream_handler = logging.StreamHandler()

    # Create formatters
    file_formatter = CustomFormatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%d.%m.%Y %H:%M:%S")
    console_formatter = logging.Formatter("%(levelname)s %(message)s", datefmt="%H:%M:%S")

    # Set formatters to handlers
    file_handler.setFormatter(file_formatter)
    stream_handler.setFormatter(console_formatter)

    # Configure logging
    logging.basicConfig(
        level=log_level,
        handlers=[file_handler, stream_handler]
    )

    # Ensure all existing loggers use the same configuration
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        logger.handlers.clear()  # Clear existing handlers
        if isinstance(logger, logging.Logger):
            logger.addHandler(file_handler)
            logger.addHandler(stream_handler)
            logger.setLevel(log_level)
