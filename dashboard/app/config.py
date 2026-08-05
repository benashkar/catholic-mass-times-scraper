"""
Configuration for the Catholic Church Dashboard.
Loads settings from environment variables.
"""

import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    # Login sessions. SECRET_KEY must be stable across restarts and gunicorn
    # workers or everyone gets logged out on each deploy.
    PERMANENT_SESSION_LIFETIME = timedelta(
        days=int(os.environ.get("SESSION_DAYS", "30"))
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Render terminates TLS in front of the app; cookies should be HTTPS-only
    # there but not locally over plain http.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
