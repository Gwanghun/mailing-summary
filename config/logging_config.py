"""
Logging configuration for the mailing_summary application.

Sets up:
- A console handler (stdout)
- A date-rotating file handler writing to logs/app_YYYY-MM-DD.log
- A dedicated error-only file handler writing to logs/errors.log

Call :func:`setup_logging` once at application startup.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOG_DIR = Path("logs")
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(module)s | %(message)s"


def setup_logging(
    log_level: str = "INFO",
    log_dir: str | Path = _LOG_DIR,
    app_name: str = "mailing_summary",
) -> None:
    """Configure root logger with console + rotating file + error-file handlers.

    After this call every ``logging.getLogger(__name__)`` in the application
    will emit to all three sinks at the appropriate level.

    Parameters
    ----------
    log_level:
        Minimum log level for the root logger and console handler.
        One of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
    log_dir:
        Directory where log files are written. Created automatically if it
        does not exist.
    app_name:
        Base name used as the rotating log file prefix.

    Notes
    -----
    Calling this function more than once is safe: it clears existing handlers
    on the root logger before adding new ones, so the configuration is
    idempotent.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ------------------------------------------------------------------
    # Console handler
    # ------------------------------------------------------------------
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    # ------------------------------------------------------------------
    # Daily-rotating application log  (logs/app_YYYY-MM-DD.log)
    # ------------------------------------------------------------------
    rotating_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / f"{app_name}.log",
        when="midnight",
        interval=1,
        backupCount=30,  # keep 30 days of history
        encoding="utf-8",
        utc=True,
    )
    # Rename rotated files to include the date suffix in the filename
    rotating_file_handler.suffix = "%Y-%m-%d"
    rotating_file_handler.setLevel(numeric_level)
    rotating_file_handler.setFormatter(formatter)

    # ------------------------------------------------------------------
    # Error-only file handler  (logs/errors.log)
    # ------------------------------------------------------------------
    error_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / "errors.log",
        when="midnight",
        interval=1,
        backupCount=90,  # keep 90 days of error history
        encoding="utf-8",
        utc=True,
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)

    # ------------------------------------------------------------------
    # Root logger assembly
    # ------------------------------------------------------------------
    root_logger = logging.getLogger()
    # Clear any handlers added by previous calls or third-party libraries
    root_logger.handlers.clear()
    root_logger.setLevel(numeric_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(rotating_file_handler)
    root_logger.addHandler(error_file_handler)

    # Suppress overly verbose loggers from third-party libraries
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised | level=%s | log_dir=%s",
        log_level.upper(),
        log_dir.resolve(),
    )
