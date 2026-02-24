"""
Configuration for the Catholic Church Dashboard.
Loads settings from environment variables.
"""

import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    # Path to the data/output/ directory containing state subdirectories
    # On Render (Docker), this will be /app/data/output
    # Locally, it's relative to the project root
    DATA_DIR = os.environ.get(
        "DATA_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "output"),
    )
