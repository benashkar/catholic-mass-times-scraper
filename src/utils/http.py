"""
http.py

HTTP request wrapper for the Catholic Mass Times Scraper.

This module provides a single function, fetch_page(), that handles all the
details of making HTTP requests:
    - Sends polite headers (User-Agent identifying who we are)
    - Retries failed requests automatically
    - Waits between requests to avoid overwhelming the server (rate limiting)
    - Logs every request for debugging

WHY A WRAPPER:
    Instead of calling requests.get() directly in every scraper file, we funnel
    all HTTP requests through this one function. This means:
    - Rate limiting is enforced everywhere automatically
    - Logging is consistent
    - If we need to change how requests work (add proxy, change headers), we
      change it in one place

HOW TO USE:
    from src.utils.http import fetch_page

    html = fetch_page("https://catholicindex.org/churches/us-oh-grove-city-...")
    if html:
        # parse the HTML
    else:
        # request failed after all retries
"""

import time

import requests

from config.settings import (
    MAX_RETRIES,
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    SCRAPE_DELAY,
)
from src.utils.logger import get_logger

# Create a logger for this module — log messages will show "src.utils.http"
logger = get_logger(__name__)

# Track the timestamp of the last request so we can enforce rate limiting.
# This is a module-level variable so it persists across multiple calls.
_last_request_time = 0.0


def fetch_page(url: str, extra_headers: dict = None) -> str | None:
    """
    Fetch a web page and return its HTML content as a string.

    This function handles rate limiting, retries, and error logging so the
    caller doesn't have to worry about any of that.

    Args:
        url: The full URL to fetch (e.g., "https://catholicindex.org/churches/...")
        extra_headers: Optional dictionary of additional HTTP headers to send.
                       These are merged with the default headers from settings.

    Returns:
        The HTML content of the page as a string, or None if the request
        failed after all retries.

    Example:
        html = fetch_page("https://catholicindex.org/mass-times/grove-city-ohio")
        if html:
            soup = BeautifulSoup(html, "lxml")
            # ... parse the page
        else:
            print("Failed to fetch page")
    """
    global _last_request_time

    # --- Rate Limiting ---
    # Calculate how long since our last request and wait if needed.
    # This prevents us from hammering the server with rapid-fire requests.
    elapsed = time.time() - _last_request_time
    if elapsed < SCRAPE_DELAY:
        wait_time = SCRAPE_DELAY - elapsed
        logger.debug(f"Rate limiting: waiting {wait_time:.1f}s before next request")
        time.sleep(wait_time)

    # --- Merge Headers ---
    # Start with our default headers and add any extras the caller provided
    headers = {**REQUEST_HEADERS}
    if extra_headers:
        headers.update(extra_headers)

    # --- Retry Loop ---
    # Try the request up to MAX_RETRIES times. Some failures are temporary
    # (server overloaded, network glitch) and succeed on retry.
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(f"Fetching (attempt {attempt}/{MAX_RETRIES}): {url}")

            # Record when we made this request (for rate limiting the NEXT request)
            _last_request_time = time.time()

            # Make the actual HTTP request
            response = requests.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            # Log the HTTP status code and how long it took
            elapsed_ms = (time.time() - _last_request_time) * 1000
            logger.info(f"HTTP {response.status_code} in {elapsed_ms:.0f}ms: {url}")

            # raise_for_status() throws an exception if the status code is 4xx or 5xx
            # (meaning the server returned an error)
            response.raise_for_status()

            # Success! Return the page content.
            return response.text

        except requests.exceptions.HTTPError as e:
            # The server returned an error code (404, 500, etc.)
            status_code = response.status_code if response is not None else None

            # Don't retry 404 errors — the page simply doesn't exist.
            # Retrying won't help and just wastes time and server resources.
            if status_code == 404:
                logger.warning(f"HTTP 404 (page not found): {url}")
                return None

            logger.warning(f"HTTP error on attempt {attempt}/{MAX_RETRIES} for {url}: {e}")
            if attempt < MAX_RETRIES:
                # Wait a bit longer before retrying (exponential backoff)
                # Attempt 1: wait 2s, Attempt 2: wait 4s, etc.
                backoff = 2**attempt
                logger.debug(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        except requests.exceptions.ConnectionError as e:
            # Could not connect to the server at all (DNS failure, server down, etc.)
            logger.warning(f"Connection error on attempt {attempt}/{MAX_RETRIES} for {url}: {e}")
            if attempt < MAX_RETRIES:
                backoff = 2**attempt
                time.sleep(backoff)

        except requests.exceptions.Timeout:
            # The server took too long to respond
            logger.warning(
                f"Timeout on attempt {attempt}/{MAX_RETRIES} for {url} (>{REQUEST_TIMEOUT}s)"
            )
            if attempt < MAX_RETRIES:
                backoff = 2**attempt
                time.sleep(backoff)

        except requests.exceptions.RequestException as e:
            # Catch-all for any other request error
            logger.error(
                f"Unexpected request error on attempt {attempt}/{MAX_RETRIES} for {url}: {e}"
            )
            if attempt < MAX_RETRIES:
                backoff = 2**attempt
                time.sleep(backoff)

    # If we get here, all retries failed
    logger.error(f"All {MAX_RETRIES} attempts failed for: {url}")
    return None
