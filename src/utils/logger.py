"""
logger.py

Sets up logging for the Catholic Mass Times Scraper.

This module configures Python's built-in logging system to write messages
to two places at the same time:
    1. The console (your terminal) — shows INFO and above so you can watch progress
    2. A log file — shows DEBUG and above for detailed troubleshooting later

HOW TO USE IN OTHER FILES:
    from src.utils.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Starting scrape for Grove City")
    logger.debug("Fetching URL: https://catholicindex.org/...")
    logger.warning("Church page returned empty mass times")
    logger.error("Failed to fetch page: HTTP 500")

HOW TO READ LOG FILES:
    Log files are saved in the logs/ folder with names like:
        scrape_2026-02-20.log

    Each line looks like:
        2026-02-20 14:30:15 | INFO     | src.scrapers.catholic_index | Starting scrape for Grove City
        ^timestamp            ^level     ^which module wrote this       ^the actual message

    Levels from least to most severe:
        DEBUG    — Very detailed info, only in log files (not console)
        INFO     — Normal operations ("scraping church X", "found 5 mass times")
        WARNING  — Something unexpected but not broken ("no mass times found for church Y")
        ERROR    — Something broke ("HTTP 500 from server", "parse failed")
        CRITICAL — The whole scraper can't continue ("config file missing")
"""

import logging
import sys
from datetime import datetime

# Import settings — we need log levels and the logs directory path
from config.settings import LOG_LEVEL_CONSOLE, LOG_LEVEL_FILE, LOGS_DIR


def get_logger(name: str) -> logging.Logger:
    """
    Create and return a configured logger.

    This function sets up a logger that writes to both the console and a daily
    log file. If the logger has already been configured (e.g., called twice
    from the same module), it returns the existing logger without adding
    duplicate handlers.

    Args:
        name: The name of the logger — typically use __name__ so the logger
              is named after the module that created it. This name appears
              in log output so you know which file generated each message.

    Returns:
        A configured logging.Logger instance ready to use.

    Example:
        logger = get_logger(__name__)
        logger.info("Hello from the scraper!")
    """
    logger = logging.getLogger(name)

    # If this logger already has handlers, it was already set up — don't add more.
    # Without this check, calling get_logger() twice would create duplicate log lines.
    if logger.handlers:
        return logger

    # Set the logger to DEBUG so it captures everything.
    # The individual handlers (console, file) will filter to their own levels.
    logger.setLevel(logging.DEBUG)

    # --- Log Format ---
    # This defines what each log line looks like.
    # %(asctime)s      → Timestamp like "2026-02-20 14:30:15"
    # %(levelname)-8s   → Level name padded to 8 chars for alignment (e.g., "INFO    ")
    # %(name)s          → Logger name (the module that created the log)
    # %(message)s       → The actual log message
    log_format = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # --- Console Handler ---
    # Prints log messages to your terminal in real time.
    # Only shows INFO and above by default (skip DEBUG noise).
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, LOG_LEVEL_CONSOLE.upper(), logging.INFO))
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)

    # --- File Handler ---
    # Writes log messages to a file in the logs/ folder.
    # Creates a new file each day (e.g., scrape_2026-02-20.log).
    # Automatically deletes log files older than LOG_RETENTION_DAYS.
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # TimedRotatingFileHandler creates a new log file at midnight each day
    # and keeps the last `backupCount` days of logs
    today = datetime.now().strftime("%Y-%m-%d")
    log_file_path = LOGS_DIR / f"scrape_{today}.log"

    file_handler = logging.FileHandler(
        filename=log_file_path,
        mode="a",  # "a" = append (don't overwrite if file exists)
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, LOG_LEVEL_FILE.upper(), logging.DEBUG))
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)

    return logger
