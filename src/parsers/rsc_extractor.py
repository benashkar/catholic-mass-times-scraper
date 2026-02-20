"""
rsc_extractor.py

Extracts structured JSON data from Next.js React Server Component (RSC) payloads.

WHAT THIS DOES:
    CatholicIndex.org is built with Next.js App Router, which embeds page data
    as "RSC flight data" inside <script> tags in the HTML. This module extracts
    that embedded JSON so we don't have to parse HTML at all — we get clean,
    structured data directly.

HOW IT WORKS:
    1. The HTML page contains <script> tags with: self.__next_f.push([1, "..."])
    2. Inside those pushes is serialized React Server Component data
    3. That data contains JSON objects with church info, mass times, etc.
    4. We extract and parse those JSON objects

WHY NOT PARSE HTML:
    The visible HTML on CatholicIndex is rendered by React (client-side).
    The actual data is embedded in the RSC payload, which is much easier to
    parse than the rendered HTML. We get clean field names, proper types,
    and structured arrays — no BeautifulSoup needed for data extraction.

USAGE:
    from src.parsers.rsc_extractor import extract_rsc_chunks, extract_initial_churches, extract_church_detail

    html = fetch_page("https://catholicindex.org/mass-times/grove-city-ohio")
    chunks = extract_rsc_chunks(html)
    churches = extract_initial_churches(chunks)

    html = fetch_page("https://catholicindex.org/churches/us-oh-grove-city-...")
    chunks = extract_rsc_chunks(html)
    detail = extract_church_detail(chunks)
"""

import json
import re
from src.utils.logger import get_logger

logger = get_logger(__name__)


def extract_rsc_chunks(html: str) -> list[str]:
    """
    Extract all React Server Component data chunks from a page's HTML.

    Next.js App Router embeds data in <script> tags like:
        self.__next_f.push([1, "...data..."])

    This function finds all those push calls and returns the string payloads.

    Args:
        html: The full HTML source of a CatholicIndex page.

    Returns:
        A list of strings, each being one RSC data chunk. These chunks contain
        serialized React component trees and JSON data.

    Example:
        html = fetch_page("https://catholicindex.org/mass-times/grove-city-ohio")
        chunks = extract_rsc_chunks(html)
        # chunks is a list of strings containing the embedded data
    """
    # Pattern explanation:
    # self.__next_f.push(  — the function call prefix
    # \[1,"               — array with type 1 (data chunk) and opening quote
    # (.*?)               — capture the content (non-greedy)
    # "\]                 — closing quote and bracket
    # )                   — closing paren
    #
    # re.DOTALL makes . match newlines too, since some chunks span multiple lines
    pattern = r'self\.__next_f\.push\(\[1,"(.*?)"\]\)'
    matches = re.findall(pattern, html, re.DOTALL)

    if not matches:
        logger.warning("No RSC data chunks found in HTML")
        return []

    # Unescape the JavaScript string encoding.
    # The RSC data is inside a JS string literal: push([1, "...escaped content..."])
    # We use json.loads to properly decode the JS string escaping, which handles
    # all escape sequences correctly: \", \\, \n, \t, \uXXXX, etc.
    decoded_chunks = []
    for match in matches:
        try:
            # Wrap in quotes so json.loads treats it as a JSON string to decode
            decoded = json.loads(f'"{match}"')
            decoded_chunks.append(decoded)
        except json.JSONDecodeError:
            # Fallback: manual unescaping for chunks that aren't valid JSON strings
            # (some chunks contain raw data that breaks json.loads)
            decoded = match.replace('\\"', '"')
            decoded = decoded.replace('\\n', '\n')
            decoded = decoded.replace('\\/', '/')
            decoded = decoded.replace('\\u0026', '&')
            # WARNING: \\  must be replaced last to avoid double-processing
            decoded = decoded.replace('\\\\', '\\')
            decoded_chunks.append(decoded)

    logger.debug(f"Extracted {len(decoded_chunks)} RSC chunks from HTML")
    return decoded_chunks


def _find_json_object(text: str, start_marker: str) -> dict | list | None:
    """
    Find and parse a JSON object/array in text starting after a marker string.

    This is a helper function that finds a marker like '"initialChurches":[' in
    the RSC data, then extracts and parses the JSON that follows it.

    Args:
        text: The text to search in (an RSC chunk).
        start_marker: A string that appears just before the JSON we want.

    Returns:
        The parsed JSON (dict or list), or None if not found.
    """
    idx = text.find(start_marker)
    if idx == -1:
        return None

    # Move to the start of the JSON value (after the marker)
    json_start = idx + len(start_marker)

    # Determine if we're looking for an object {...} or array [...]
    # Back up to find the opening bracket/brace
    # The marker typically ends with ":[ or ":{
    # We need to include the opening bracket
    if start_marker.endswith('['):
        open_char = '['
        close_char = ']'
        json_start = json_start - 1  # Include the [
    elif start_marker.endswith('{'):
        open_char = '{'
        close_char = '}'
        json_start = json_start - 1  # Include the {
    else:
        # Look for the next { or [
        for i in range(json_start, min(json_start + 10, len(text))):
            if text[i] in ('{', '['):
                open_char = text[i]
                close_char = '}' if open_char == '{' else ']'
                json_start = i
                break
        else:
            return None

    # Walk through the text tracking bracket depth to find the matching close
    depth = 0
    # We also need to handle strings properly (don't count brackets inside strings)
    in_string = False
    escape_next = False

    for i in range(json_start, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                json_str = text[json_start:i + 1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse JSON after '{start_marker}': {e}")
                    return None

    logger.warning(f"Could not find matching close bracket for '{start_marker}'")
    return None


def extract_initial_churches(chunks: list[str]) -> list[dict]:
    """
    Extract the list of churches from a city/mass-times page's RSC data.

    On city pages like /mass-times/grove-city-ohio, the RSC data contains an
    "initialChurches" array with all churches within the search radius. Each
    church has basic info (name, address, coordinates) plus upcoming mass times.

    Args:
        chunks: List of RSC data chunks from extract_rsc_chunks().

    Returns:
        A list of church dictionaries. Each church has these fields:
            - slug (str): URL slug like "us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf"
            - name (str): Church name like "Our Lady of Perpetual Help Church"
            - street (str): Street address
            - city (str): City name
            - stateRegion (str): State (e.g., "Ohio")
            - latitude (float): Latitude coordinate
            - longitude (float): Longitude coordinate
            - phone (str): Phone number
            - website (str): Website URL
            - distanceMiles (str): Distance from city center
            - massCount (int): Total number of weekly masses
            - confessionCount (int): Total confession time slots
            - adorationCount (int): Total adoration time slots
            - upcomingMasses (list): Next few upcoming masses
            - upcomingConfessions (list): Next few upcoming confessions
            - upcomingAdorations (list): Next few upcoming adorations
            - hasPerpetualAdoration (bool): Whether the church has 24/7 adoration

        Returns an empty list if no churches found.

    Example:
        html = fetch_page("https://catholicindex.org/mass-times/grove-city-ohio")
        chunks = extract_rsc_chunks(html)
        churches = extract_initial_churches(chunks)
        for church in churches:
            print(f"{church['name']} — {church['city']}, {church['stateRegion']}")
    """
    # Search through all chunks for the initialChurches array
    for chunk in chunks:
        if '"initialChurches"' not in chunk:
            continue

        churches = _find_json_object(chunk, '"initialChurches":[')
        if churches:
            logger.info(f"Found {len(churches)} churches in RSC data")
            return churches

    logger.warning("No initialChurches found in RSC data")
    return []


def extract_city_metadata(chunks: list[str]) -> dict:
    """
    Extract the city-level metadata from a mass-times page's RSC data.

    This includes the latitude/longitude center point, radius, city name, etc.

    Args:
        chunks: List of RSC data chunks from extract_rsc_chunks().

    Returns:
        A dictionary with city metadata fields:
            - latitude (float): Center latitude
            - longitude (float): Center longitude
            - radius (int): Search radius in miles
            - contextLabel (str): City name like "Grove City, Ohio"
            - citySlug (str): URL slug like "grove-city-ohio"
            - timeZone (str): IANA timezone like "America/New_York"

        Returns an empty dict if not found.
    """
    for chunk in chunks:
        # Look for the session component props which contain city metadata
        if '"contextLabel"' not in chunk:
            continue

        # Extract individual fields using regex since they're embedded in RSC format
        metadata = {}

        # Extract latitude
        lat_match = re.search(r'"latitude":([\d.-]+)', chunk)
        if lat_match:
            metadata['latitude'] = float(lat_match.group(1))

        # Extract longitude
        lon_match = re.search(r'"longitude":([\d.-]+)', chunk)
        if lon_match:
            metadata['longitude'] = float(lon_match.group(1))

        # Extract radius
        radius_match = re.search(r'"radius":(\d+)', chunk)
        if radius_match:
            metadata['radius'] = int(radius_match.group(1))

        # Extract context label (city name)
        label_match = re.search(r'"contextLabel":"([^"]+)"', chunk)
        if label_match:
            metadata['contextLabel'] = label_match.group(1)

        # Extract city slug
        slug_match = re.search(r'"citySlug":"([^"]+)"', chunk)
        if slug_match:
            metadata['citySlug'] = slug_match.group(1)

        # Extract timezone
        tz_match = re.search(r'"timeZone":"([^"]+)"', chunk)
        if tz_match:
            metadata['timeZone'] = tz_match.group(1)

        if metadata:
            logger.debug(f"Extracted city metadata: {metadata.get('contextLabel', 'unknown')}")
            return metadata

    logger.warning("No city metadata found in RSC data")
    return {}


def extract_church_detail(chunks: list[str]) -> dict | None:
    """
    Extract the full church detail data from an individual church page's RSC data.

    On church pages like /churches/us-oh-grove-city-our-lady-..., the RSC data
    contains a "data" prop with the complete church record including all services
    (mass times, confession, adoration, devotions), organized by category.

    Args:
        chunks: List of RSC data chunks from extract_rsc_chunks().

    Returns:
        A dictionary with the full church detail:
            - church (dict): Church info (slug, name, address, phone, etc.)
            - services (dict): Services organized by category:
                - Mass (list): Mass times with day, time, type, notes
                - Confession (list): Confession times with day, start/end times
                - Adoration (list): Adoration hours
                - Devotions (list): Other devotions (rosary, novena, etc.)
            - totalServices (int): Total service count
            - communityInsights (str): Visitor reviews/insights

        Each service in the lists has these fields:
            - serviceId (str): Unique identifier
            - category (str): "Mass", "Confession", "Adoration", or "Devotions"
            - scheduleType (str): "sunday", "saturday", "specific_weekday",
                                  "monthly", "special_event", "other"
            - dayOfWeek (str|null): "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"
            - timeStart (str|null): Time in "HH:MM:SS" format (24-hour)
            - timeEnd (str|null): End time (for confession/adoration ranges)
            - displayName (str): Human-readable name like "Holy Mass" or "Vigil"
            - language (str|null): Language if non-English
            - notes (str|null): Additional notes
            - pattern (str|null): For monthly services, e.g., "first_friday_(recurring)"
            - eventDate (str|null): For special events, e.g., "2025-12-08"

        Returns None if no church detail found.

    Example:
        html = fetch_page("https://catholicindex.org/churches/us-oh-grove-city-...")
        chunks = extract_rsc_chunks(html)
        detail = extract_church_detail(chunks)
        if detail:
            for mass in detail['services'].get('Mass', []):
                print(f"  {mass['dayOfWeek']} {mass['timeStart']} — {mass['displayName']}")
    """
    for chunk in chunks:
        # The church detail is inside a component prop: {"data":{"church":{...},"services":{...}}}
        if '"services"' not in chunk or '"church"' not in chunk:
            continue

        # Look for the data prop containing church and services
        # Pattern: {"data":{"church":{...},"services":{...},...}}
        data = _find_json_object(chunk, '"data":{')
        if data and 'church' in data and 'services' in data:
            church_name = data['church'].get('name', 'unknown')
            service_count = data.get('totalServices', 0)
            logger.info(f"Extracted detail for {church_name} ({service_count} services)")

            # Clean up the "$undefined" values that Next.js RSC uses for undefined
            _clean_undefined(data)

            return data

    logger.warning("No church detail data found in RSC chunks")
    return None


def _clean_undefined(obj):
    """
    Recursively replace "$undefined" string values with None.

    Next.js RSC serializes JavaScript `undefined` as the string "$undefined".
    This function walks through the data and replaces those with Python None
    for cleaner data handling.

    Args:
        obj: A dict, list, or primitive value to clean.
    """
    if isinstance(obj, dict):
        for key in obj:
            if obj[key] == "$undefined":
                obj[key] = None
            else:
                _clean_undefined(obj[key])
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if item == "$undefined":
                obj[i] = None
            else:
                _clean_undefined(item)
