"""
Configuration for the Catholic Church Dashboard.
Loads settings from environment variables.
"""

import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
