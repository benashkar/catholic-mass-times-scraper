"""
settings.py

Central configuration file for the Catholic Mass Times Scraper.

This file loads settings from the .env file (for secrets and environment-specific
values) and defines constants used throughout the project.

HOW IT WORKS:
    1. python-dotenv reads the .env file in the project root
    2. os.getenv() fetches each value (with a sensible default if missing)
    3. Other modules import from here:  from config.settings import SCRAPE_DELAY

WHY A SEPARATE FILE:
    Keeping all settings in one place means you never have to hunt through
    multiple files to change a URL or timeout value. If CatholicIndex changes
    their domain, you change it here and everything updates.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Project Paths
# ---------------------------------------------------------------------------
# Path.resolve() gives us the absolute path, so the project works no matter
# what directory you run it from.
# __file__ is THIS file (settings.py), so we go up two levels to get the
# project root: config/settings.py -> config/ -> project_root/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load environment variables from the .env file in the project root
# override=False means .env values won't overwrite existing system env vars
load_dotenv(PROJECT_ROOT / ".env", override=False)

# --- Directory Paths ---
DATA_DIR = PROJECT_ROOT / "data"
CHURCHES_DIR = DATA_DIR / "churches"
OUTPUT_DIR = DATA_DIR / "output"
LOGS_DIR = PROJECT_ROOT / "logs"
TESTS_DIR = PROJECT_ROOT / "tests"
FIXTURES_DIR = TESTS_DIR / "fixtures"

# ---------------------------------------------------------------------------
# Scraper Settings
# ---------------------------------------------------------------------------

# How long to wait (in seconds) between HTTP requests to the same site.
# This is called "rate limiting" — it prevents us from overwhelming the server.
# 1.5 seconds is polite; reduce only if the site explicitly allows faster access.
SCRAPE_DELAY = float(os.getenv("SCRAPE_DELAY_SECONDS", "1.5"))

# How many times to retry a failed HTTP request before giving up.
# Websites sometimes have temporary glitches — retrying usually fixes it.
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# The User-Agent header tells the website who is making the request.
# Being transparent about who we are is good etiquette and helps site admins
# contact us if there's a problem, instead of just blocking our IP.
USER_AGENT = os.getenv(
    "USER_AGENT",
    "CatholicMassTimesScraper/1.0 (CR Community News; contact: ben@healthyanalytics.com)"
)

# Standard headers sent with every HTTP request
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# HTTP request timeout in seconds — if a server doesn't respond in this time,
# give up rather than hanging forever
REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Data Source URLs
# ---------------------------------------------------------------------------

# CatholicIndex.org — our primary data source (Tier 1)
# Server-side rendered HTML, so we can use simple requests + BeautifulSoup
CATHOLIC_INDEX_BASE_URL = "https://catholicindex.org"
CATHOLIC_INDEX_SEARCH_URL = f"{CATHOLIC_INDEX_BASE_URL}/search"
CATHOLIC_INDEX_MASS_TIMES_URL = f"{CATHOLIC_INDEX_BASE_URL}/mass-times"

# DiscoverMass.com — backup data source (Tier 2)
DISCOVER_MASS_BASE_URL = "https://discovermass.com"

# ---------------------------------------------------------------------------
# Target Communities
# ---------------------------------------------------------------------------
# The 16 Ohio communities we're scraping for Phase 1.
# Each entry is a dict with the community name, county, and relevant ZIP codes.
# ZIP codes are critical for small towns that may not have their own parish —
# we search by ZIP to find the nearest serving church.
TARGET_COMMUNITIES = [
    # Communities WITH their own CatholicIndex city page:
    {"name": "Canal Winchester", "county": "Franklin", "state": "OH", "zips": ["43110"]},
    {"name": "Groveport", "county": "Franklin", "state": "OH", "zips": ["43125"]},
    {"name": "Hilliard", "county": "Franklin", "state": "OH", "zips": ["43026"]},
    {"name": "Grove City", "county": "Franklin", "state": "OH", "zips": ["43123"]},

    # Communities WITHOUT their own CatholicIndex city page.
    # "fallback_city" tells the scraper which nearby city page to use instead.
    # We then filter results by distance to only include nearby churches.
    {"name": "Obetz", "county": "Franklin", "state": "OH", "zips": ["43207"],
     "fallback_city": "Columbus"},
    {"name": "Hamilton Township", "county": "Franklin", "state": "OH", "zips": ["43137"],
     "fallback_city": "Columbus"},
    {"name": "Lithopolis", "county": "Fairfield", "state": "OH", "zips": ["43136"],
     "fallback_city": "Columbus"},
    {"name": "Lockbourne", "county": "Franklin", "state": "OH", "zips": ["43137"],
     "fallback_city": "Columbus"},
    {"name": "Madison Township", "county": "Franklin", "state": "OH", "zips": ["43110", "43125"],
     "fallback_city": "Columbus"},
    {"name": "West Columbus", "county": "Franklin", "state": "OH", "zips": ["43204", "43223"],
     "fallback_city": "Columbus"},
    {"name": "Lincoln Village", "county": "Franklin", "state": "OH", "zips": ["43228"],
     "fallback_city": "Columbus"},
    {"name": "Prairie Township", "county": "Franklin", "state": "OH", "zips": ["43123", "43228"],
     "fallback_city": "Columbus"},
    {"name": "Westgate", "county": "Franklin", "state": "OH", "zips": ["43204"],
     "fallback_city": "Columbus"},
    {"name": "Galloway", "county": "Franklin", "state": "OH", "zips": ["43119"],
     "fallback_city": "Columbus"},
    {"name": "Urbancrest", "county": "Franklin", "state": "OH", "zips": ["43123"],
     "fallback_city": "Columbus"},
    {"name": "Commercial Point", "county": "Pickaway", "state": "OH", "zips": ["43116"],
     "fallback_city": "Columbus"},
]

# ---------------------------------------------------------------------------
# Logging Settings
# ---------------------------------------------------------------------------

# Log levels control how much detail you see.
# DEBUG = everything (very verbose), INFO = normal operations,
# WARNING = potential problems, ERROR = things that broke, CRITICAL = show-stoppers
LOG_LEVEL_CONSOLE = os.getenv("LOG_LEVEL_CONSOLE", "INFO")
LOG_LEVEL_FILE = os.getenv("LOG_LEVEL_FILE", "DEBUG")

# How many days of log files to keep before automatically deleting old ones
LOG_RETENTION_DAYS = 30
