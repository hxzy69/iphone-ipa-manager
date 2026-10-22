"""Logging: ASCII-safe console output (cmd.exe friendly) + rotating file."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from iphone_detector.paths import DATA_ROOT


def setup_logging(log_file: str = "iphone_detector.log", verbose: bool = False) -> None:
    """Attach a console handler and a rotating file handler to the logger."""
    logger = logging.getLogger("iphone-detector")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if logger.handlers:  # avoid duplicated handlers on re-init
        return

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        logger.addHandler(console)

    file_path = DATA_ROOT / log_file
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            file_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Could not open log file %s - logging to console only", file_path)
