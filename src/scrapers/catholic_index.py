"""
catholic_index.py

Scraper for CatholicIndex.org — our primary data source (Tier 1).

This module provides functions to:
    1. Discover churches serving a community (via city/mass-times pages)
    2. Scrape full schedules for individual churches (via church detail pages)

HOW IT WORKS:
    CatholicIndex is a Next.js app that embeds all church data as React Server
    Component (RSC) payloads in the HTML. We fetch the page, extract the RSC
    data, and parse it as JSON — no HTML parsing needed for the actual data.

URL PATTERNS:
    City pages:    https://catholicindex.org/mass-times/{city-slug}-{state}
                   Example: /mass-times/grove-city-ohio
    Church pages:  https://catholicindex.org/churches/{slug}
                   Example: /churches/us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf

USAGE:
    from src.scrapers.catholic_index import discover_churches, scrape_church_detail

    # Discover all churches near a community
    churches = discover_churches("Grove City", "OH")

    # Get full schedule for a specific church
    detail = scrape_church_detail("us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf")
"""

from datetime import UTC, datetime

from config.settings import CATHOLIC_INDEX_BASE_URL, CATHOLIC_INDEX_MASS_TIMES_URL
from src.parsers.rsc_extractor import (
    extract_church_detail,
    extract_city_metadata,
    extract_initial_churches,
    extract_rsc_chunks,
)
from src.utils.http import fetch_page
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _city_to_slug(city: str, state: str = "ohio") -> str:
    """
    Convert a city name and state to a CatholicIndex URL slug.

    CatholicIndex uses lowercase, hyphen-separated city names in their URLs.
    For example, "Grove City" + "OH" becomes "grove-city-ohio".

    Args:
        city: The city name (e.g., "Grove City", "Canal Winchester")
        state: The state abbreviation or full name (e.g., "OH" or "ohio")

    Returns:
        The URL slug (e.g., "grove-city-ohio")

    Example:
        slug = _city_to_slug("Canal Winchester", "OH")
        # Returns: "canal-winchester-ohio"
    """
    # Map state abbreviations to full names (lowercase, hyphenated for URLs)
    # CatholicIndex URL format: /mass-times/{city}-{state}
    state_map = {
        "AL": "alabama",
        "AK": "alaska",
        "AZ": "arizona",
        "AR": "arkansas",
        "CA": "california",
        "CO": "colorado",
        "CT": "connecticut",
        "DE": "delaware",
        "FL": "florida",
        "GA": "georgia",
        "HI": "hawaii",
        "ID": "idaho",
        "IL": "illinois",
        "IN": "indiana",
        "IA": "iowa",
        "KS": "kansas",
        "KY": "kentucky",
        "LA": "louisiana",
        "ME": "maine",
        "MD": "maryland",
        "MA": "massachusetts",
        "MI": "michigan",
        "MN": "minnesota",
        "MS": "mississippi",
        "MO": "missouri",
        "MT": "montana",
        "NE": "nebraska",
        "NV": "nevada",
        "NH": "new-hampshire",
        "NJ": "new-jersey",
        "NM": "new-mexico",
        "NY": "new-york",
        "NC": "north-carolina",
        "ND": "north-dakota",
        "OH": "ohio",
        "OK": "oklahoma",
        "OR": "oregon",
        "PA": "pennsylvania",
        "RI": "rhode-island",
        "SC": "south-carolina",
        "SD": "south-dakota",
        "TN": "tennessee",
        "TX": "texas",
        "UT": "utah",
        "VT": "vermont",
        "VA": "virginia",
        "WA": "washington",
        "WV": "west-virginia",
        "WI": "wisconsin",
        "WY": "wyoming",
        "DC": "district-of-columbia",
    }

    # Convert state abbreviation to full name if needed
    state_full = state_map.get(state.upper(), state.lower())

    # Convert city name: lowercase, replace spaces with hyphens
    city_slug = city.lower().replace(" ", "-")

    return f"{city_slug}-{state_full}"


def discover_churches(city: str, state: str = "OH") -> list[dict]:
    """
    Discover all Catholic churches serving a community.

    Fetches the CatholicIndex city mass-times page and extracts the list
    of churches within the default 25-mile radius. Each church includes
    basic info (name, address, coordinates) and upcoming mass counts.

    Args:
        city: The community name (e.g., "Grove City")
        state: The state abbreviation (default: "OH" for Ohio)

    Returns:
        A list of church dictionaries, each containing:
            - slug: URL-safe identifier for the church
            - name: Full parish name
            - street: Street address
            - city: City where the church is located
            - stateRegion: State name
            - latitude/longitude: Coordinates
            - phone: Phone number
            - website: Church website URL
            - distanceMiles: Distance from the searched city center
            - massCount: Number of weekly masses
            - confessionCount: Number of confession time slots
            - adorationCount: Number of adoration slots
            - hasPerpetualAdoration: Whether 24/7 adoration is offered
            - source_url: The URL this data was scraped from
            - last_scraped: Timestamp of when we scraped this

        Returns an empty list if the page couldn't be fetched or parsed.

    Example:
        churches = discover_churches("Grove City", "OH")
        for c in churches:
            print(f"{c['name']} — {c['city']}, {c['stateRegion']} ({c['distanceMiles']} mi)")
    """
    # Build the URL for this city's mass times page
    slug = _city_to_slug(city, state)
    url = f"{CATHOLIC_INDEX_MASS_TIMES_URL}/{slug}"

    logger.info(f"Discovering churches for {city}, {state} at {url}")

    # Fetch the page HTML
    html = fetch_page(url)
    if not html:
        logger.error(f"Failed to fetch city page for {city}, {state}")
        return []

    # Extract RSC data from the HTML
    chunks = extract_rsc_chunks(html)
    if not chunks:
        logger.error(f"No RSC data found in page for {city}, {state}")
        return []

    # Extract the city metadata (center coordinates, radius, etc.)
    metadata = extract_city_metadata(chunks)
    if metadata:
        logger.info(
            f"City center: {metadata.get('contextLabel', city)} "
            f"({metadata.get('latitude', '?')}, {metadata.get('longitude', '?')}), "
            f"radius: {metadata.get('radius', '?')} miles"
        )

    # Extract the list of churches
    churches = extract_initial_churches(chunks)

    # Add scraping metadata to each church record
    now = datetime.now(UTC).isoformat()
    for church in churches:
        church["source_url"] = url
        church["last_scraped"] = now
        church["searched_city"] = city
        church["searched_state"] = state

    logger.info(f"Found {len(churches)} churches within radius of {city}, {state}")

    return churches


def scrape_church_detail(slug: str) -> dict | None:
    """
    Scrape the full schedule and details for a single church.

    Fetches the CatholicIndex church detail page and extracts all service
    times (mass, confession, adoration, devotions), organized by category.

    Args:
        slug: The church's URL slug (e.g., "us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf")

    Returns:
        A dictionary containing:
            - church (dict): Church info including:
                - slug, name, street, city, stateRegion, postalCode
                - latitude, longitude, phone, website
                - updatedAt: When the schedule was last updated on CatholicIndex
            - services (dict): Services organized by category:
                - Mass (list): Each mass with dayOfWeek, timeStart, displayName, etc.
                - Confession (list): Confession times with start/end
                - Adoration (list): Adoration hours
                - Devotions (list): Other services
            - totalServices (int): Total number of services
            - communityInsights (str): Visitor reviews
            - source_url (str): The URL we scraped from
            - last_scraped (str): ISO timestamp of when we scraped

        Returns None if the page couldn't be fetched or parsed.

    Example:
        detail = scrape_church_detail("us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf")
        if detail:
            print(f"Church: {detail['church']['name']}")
            print(f"Updated: {detail['church']['updatedAt']}")
            for mass in detail['services'].get('Mass', []):
                print(f"  {mass['dayOfWeek']} at {mass['timeStart']}")
    """
    url = f"{CATHOLIC_INDEX_BASE_URL}/churches/{slug}"

    logger.info(f"Scraping church detail: {slug}")

    # Fetch the page HTML
    html = fetch_page(url)
    if not html:
        logger.error(f"Failed to fetch church page: {slug}")
        return None

    # Extract RSC data from the HTML
    chunks = extract_rsc_chunks(html)
    if not chunks:
        logger.error(f"No RSC data found in church page: {slug}")
        return None

    # Extract the full church detail
    detail = extract_church_detail(chunks)
    if not detail:
        logger.error(f"Could not extract church detail from: {slug}")
        return None

    # Add scraping metadata
    detail["source_url"] = url
    detail["last_scraped"] = datetime.now(UTC).isoformat()

    church_name = detail.get("church", {}).get("name", "unknown")
    total = detail.get("totalServices", 0)
    logger.info(f"Successfully scraped {church_name}: {total} services")

    return detail


def discover_churches_by_zip(zip_code: str) -> list[dict]:
    """
    Discover churches using CatholicIndex's search by ZIP code.

    Some small communities (Obetz, Lockbourne, Urbancrest) don't have their
    own city page on CatholicIndex. For these, we search by ZIP code instead.

    NOTE: This function uses the search page which may work differently than
    city pages. It will be implemented after inspecting the search endpoint.

    Args:
        zip_code: A 5-digit US ZIP code (e.g., "43207")

    Returns:
        A list of church dictionaries (same format as discover_churches).
    """
    # TODO: Implement after inspecting CatholicIndex search endpoint
    # The search page at catholicindex.org/search may use a different
    # data loading pattern that needs investigation.
    logger.warning(f"ZIP code search not yet implemented for: {zip_code}")
    return []
