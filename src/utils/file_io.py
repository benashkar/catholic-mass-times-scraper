"""
file_io.py

Helper functions for reading and writing data files (CSV and JSON).

This module provides simple, well-documented functions for saving and loading
scraped data. It handles common pitfalls like:
    - Creating directories if they don't exist
    - Using UTF-8 encoding (for church names with accents, e.g., "St. Thérèse")
    - Writing CSV headers automatically

HOW TO USE:
    from src.utils.file_io import save_to_csv, load_from_csv, save_to_json, load_from_json

    # Save a list of church dictionaries to CSV
    churches = [{"church_name": "Our Lady of Perpetual Help", "city": "Grove City"}]
    save_to_csv(churches, "data/churches/grove_city.csv")

    # Load it back
    loaded = load_from_csv("data/churches/grove_city.csv")
"""

import csv
import json
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)


def save_to_csv(data: list[dict], file_path: str | Path) -> None:
    """
    Save a list of dictionaries to a CSV file.

    Each dictionary in the list becomes one row in the CSV. The dictionary
    keys become the column headers (taken from the first item in the list).

    Args:
        data: A list of dictionaries, where each dict has the same keys.
              Example: [{"name": "St. Mary", "city": "Columbus"}, ...]
        file_path: Where to save the CSV file. Can be a string or Path object.
                   Parent directories are created automatically if they don't exist.

    Raises:
        ValueError: If data is empty (no rows to write).

    Example:
        churches = [
            {"church_name": "Our Lady of Perpetual Help", "city": "Grove City"},
            {"church_name": "St. John XXIII", "city": "Canal Winchester"},
        ]
        save_to_csv(churches, "data/churches/master_list.csv")
    """
    if not data:
        logger.warning(f"No data to save to {file_path}")
        return

    file_path = Path(file_path)

    # Create parent directories if they don't exist
    # (e.g., if "data/churches/" doesn't exist yet, create it)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Get column headers from the keys of the first dictionary
    fieldnames = list(data[0].keys())

    # Write the CSV file
    # newline="" prevents extra blank lines on Windows
    # encoding="utf-8-sig" adds a BOM (byte order mark) so Excel opens it correctly
    with open(file_path, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()  # Write the column header row
        writer.writerows(data)  # Write all data rows

    logger.info(f"Saved {len(data)} rows to {file_path}")


def load_from_csv(file_path: str | Path) -> list[dict]:
    """
    Load a CSV file and return its contents as a list of dictionaries.

    Each row in the CSV becomes one dictionary, with column headers as keys.

    Args:
        file_path: Path to the CSV file to load.

    Returns:
        A list of dictionaries, one per row.
        Returns an empty list if the file doesn't exist.

    Example:
        churches = load_from_csv("data/churches/master_list.csv")
        for church in churches:
            print(church["church_name"])
    """
    file_path = Path(file_path)

    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return []

    with open(file_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        data = list(reader)

    logger.info(f"Loaded {len(data)} rows from {file_path}")
    return data


def save_to_json(data: dict | list, file_path: str | Path) -> None:
    """
    Save a dictionary or list to a JSON file.

    Args:
        data: The data to save (a dictionary or list of dictionaries).
        file_path: Where to save the JSON file.

    Example:
        church = {"church_name": "Our Lady of Perpetual Help", "masses": [...]}
        save_to_json(church, "data/churches/our_lady.json")
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, mode="w", encoding="utf-8") as f:
        # indent=2 makes the JSON human-readable (pretty-printed)
        # ensure_ascii=False preserves special characters (accents, etc.)
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved JSON to {file_path}")


def load_from_json(file_path: str | Path) -> dict | list | None:
    """
    Load a JSON file and return its contents.

    Args:
        file_path: Path to the JSON file to load.

    Returns:
        The parsed JSON data (dict or list), or None if the file doesn't exist.

    Example:
        church = load_from_json("data/churches/our_lady.json")
        if church:
            print(church["church_name"])
    """
    file_path = Path(file_path)

    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return None

    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)

    logger.info(f"Loaded JSON from {file_path}")
    return data
