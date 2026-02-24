"""
address_parser.py

Parses US street addresses into granular components for structured storage.

HOW IT WORKS:
    Takes a raw street address string like "300 E Highland Ave" and breaks it
    into individual parts: street_number=300, pre_direction=E, street_name=Highland,
    street_suffix=Ave. Also handles state normalization and ZIP code splitting.

    Uses a token-based approach (not a single regex) because real church addresses
    have too many variations: route addresses, directional ambiguity, "Saint" vs
    "Street" for "St", Via prefix streets, etc.

WHY NOT USE A LIBRARY:
    We use only Python standard library to avoid dependency issues. The address
    patterns in Catholic church data are well-defined enough for rule-based parsing.

USAGE:
    from src.parsers.address_parser import parse_church_address

    parsed = parse_church_address({
        "slug": "us-oh-ada-our-lady-of-lourdes-7728ad72",
        "name": "Our Lady of Lourdes",
        "street": "300 E Highland Ave",
        "city": "Ada",
        "stateRegion": "Ohio",
        "postalCode": "45810-1122",
        "latitude": 40.77245252,
        "longitude": -83.81983261,
        "phone": "(419) 675-1162",
    })
    # Returns dict with: street_number, pre_direction, street_name, street_suffix, etc.
"""

import re

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# LOOKUP TABLES
# =============================================================================

# Directional words — used to detect pre/post directional tokens
DIRECTIONALS = {
    "N",
    "S",
    "E",
    "W",
    "NE",
    "NW",
    "SE",
    "SW",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
    "NORTHEAST",
    "NORTHWEST",
    "SOUTHEAST",
    "SOUTHWEST",
}

# Normalize directional tokens to standard 1-2 letter abbreviations
DIRECTION_NORMALIZE = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
    "N": "N",
    "S": "S",
    "E": "E",
    "W": "W",
    "NE": "NE",
    "NW": "NW",
    "SE": "SE",
    "SW": "SW",
}

# USPS standard street suffixes — maps variants to canonical form
STREET_SUFFIXES = {
    "AVE": "Ave",
    "AVENUE": "Ave",
    "BLVD": "Blvd",
    "BOULEVARD": "Blvd",
    "CIR": "Cir",
    "CIRCLE": "Cir",
    "CT": "Ct",
    "COURT": "Ct",
    "DR": "Dr",
    "DRIVE": "Dr",
    "HWY": "Hwy",
    "HIGHWAY": "Hwy",
    "LN": "Ln",
    "LANE": "Ln",
    "LOOP": "Loop",
    "PASS": "Pass",
    "PATH": "Path",
    "PIKE": "Pike",
    "PKWY": "Pkwy",
    "PARKWAY": "Pkwy",
    "PL": "Pl",
    "PLACE": "Pl",
    "RD": "Rd",
    "ROAD": "Rd",
    "ST": "St",
    "STREET": "St",
    "TER": "Ter",
    "TERRACE": "Ter",
    "TRL": "Trl",
    "TRAIL": "Trl",
    "WAY": "Way",
    "EXT": "Ext",
    "EXTENSION": "Ext",
}

# Unit/apartment designators
UNIT_TYPES = {
    "APT": "Apt",
    "APARTMENT": "Apt",
    "STE": "Ste",
    "SUITE": "Ste",
    "UNIT": "Unit",
    "#": "#",
    "FL": "Fl",
    "FLOOR": "Fl",
    "RM": "Rm",
    "ROOM": "Rm",
    "BLDG": "Bldg",
    "BUILDING": "Bldg",
}

# Route patterns — these are named roads where the "name" includes a route number
# Matched as regex against the remaining tokens after street number extraction
ROUTE_PATTERNS = [
    re.compile(r"^(State\s+R(?:t|te|oute|d)|State\s+Hwy)\s+(.+)", re.IGNORECASE),
    re.compile(r"^(US\s+R(?:t|te|oute)|US\s+Hwy|U\.?S\.?\s+R(?:t|te))\s+(.+)", re.IGNORECASE),
    re.compile(r"^(County\s+R(?:d|oad)|Co\s+Rd)\s+(.+)", re.IGNORECASE),
    re.compile(r"^(SR)\s+(.+)", re.IGNORECASE),
    # State-code hyphenated routes: OH-46, IN-3, etc.
    # We capture the ENTIRE "OH-46" as one group so the hyphen is preserved
    re.compile(r"^([A-Z]{2}-\d+.*)$", re.IGNORECASE),
]

# Full state name -> 2-letter USPS code (all 50 states + DC)
STATE_NAME_TO_CODE = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    "District of Columbia": "DC",
}

# Reverse lookup: code -> code (so we can validate abbreviations too)
VALID_STATE_CODES = set(STATE_NAME_TO_CODE.values())


# =============================================================================
# PARSING FUNCTIONS
# =============================================================================


def _clean_street(street: str) -> tuple[str, str]:
    """
    Pre-clean a street address string.

    Removes parenthetical and bracket annotations, normalizes dotted
    directionals like "S.E" to "SE".

    Args:
        street: Raw street address string.

    Returns:
        Tuple of (cleaned_street, notes_extracted).
    """
    if not street:
        return ("", "")

    cleaned = street.strip()
    notes = []

    # Extract parenthetical content: "(Sansbury Hall)", "(US Rte 42)"
    paren_matches = re.findall(r"\([^)]+\)", cleaned)
    for m in paren_matches:
        notes.append(m)
        cleaned = cleaned.replace(m, "")

    # Extract bracket content: "[PO Box 219]", "[McCartyville]"
    bracket_matches = re.findall(r"\[[^\]]+\]", cleaned)
    for m in bracket_matches:
        notes.append(m)
        cleaned = cleaned.replace(m, "")

    # Normalize dotted directionals: "S.E" -> "SE", "N.E" -> "NE"
    cleaned = re.sub(r"\b([NSEW])\.([NSEW])\b", r"\1\2", cleaned)

    # Remove extra whitespace
    cleaned = " ".join(cleaned.split())

    return (cleaned, "; ".join(notes))


def _match_route(remaining: str) -> tuple[str, str] | None:
    """
    Check if the remaining address (after street number) is a route address.

    Route addresses like "State Rt 364" or "County Rd 39" have the route
    designation as the street name and no traditional suffix.

    Args:
        remaining: Tokens after the street number, joined as a string.

    Returns:
        Tuple of (route_name, post_direction) if matched, None otherwise.
        post_direction is empty string if no trailing directional.
    """
    for pattern in ROUTE_PATTERNS:
        m = pattern.match(remaining)
        if m:
            groups = m.groups()

            if len(groups) == 1:
                # Single-group pattern (e.g., state-code hyphenated: "OH-46")
                # The entire match is the route name
                route_name = groups[0].strip()
                # Check for trailing directional after a space
                tokens = route_name.split()
                if len(tokens) >= 2:
                    last = tokens[-1].upper()
                    if last in DIRECTIONALS and len(last) <= 2:
                        return (" ".join(tokens[:-1]), DIRECTION_NORMALIZE.get(last, last))
                return (route_name, "")

            # Two-group pattern (prefix + rest): "State Rt" + "364"
            prefix = groups[0]
            rest = groups[1].strip()

            # Check for trailing directional: "634 N" -> route_num="634", post_dir="N"
            rest_tokens = rest.split()
            if len(rest_tokens) >= 2:
                last = rest_tokens[-1].upper()
                if last in DIRECTIONALS and len(last) <= 2:
                    route_name = f"{prefix} {' '.join(rest_tokens[:-1])}"
                    return (route_name, DIRECTION_NORMALIZE.get(last, last))

            route_name = f"{prefix} {rest}"
            return (route_name, "")

    return None


def parse_street(street: str) -> dict:
    """
    Parse a US street address string into component parts.

    Uses a token-based approach that handles:
    - Standard addresses: "300 E Highland Ave"
    - Route addresses: "02441 State Rt 364", "1098 County Rd 39"
    - Post-directionals: "748 Roswell Rd SW", "1001 Main St E"
    - Direction-as-name: "401 N St" (N is the street name, not a direction)
    - Via prefix: "343 Via Mount Carmel Ave"
    - St=Saint: "200 St Joseph Dr" (St is "Saint", not "Street")
    - Ampersand: "109 & Cowen St"
    - Leading zeros: "02441" preserved as string

    Args:
        street: Raw street address string (e.g., "300 E Highland Ave")

    Returns:
        Dict with keys: street_number, pre_direction, street_name,
        street_suffix, post_direction, unit_type, unit_number.
        All values are strings (empty string if not present).
    """
    empty_result = {
        "street_number": "",
        "pre_direction": "",
        "street_name": "",
        "street_suffix": "",
        "post_direction": "",
        "unit_type": "",
        "unit_number": "",
    }

    if not street or not street.strip():
        return empty_result

    cleaned, _ = _clean_street(street)
    if not cleaned:
        return empty_result

    tokens = cleaned.split()
    pos = 0

    # ---- Step 1: Extract street number ----
    # Match leading digits, possibly with hyphen (e.g., "300", "02441")
    street_number = ""
    if tokens[0] and re.match(r"^\d+$", tokens[0]):
        street_number = tokens[0]
        pos = 1
    elif tokens[0] and re.match(r"^\d+-", tokens[0]):
        # Hyphenated number like "13-779" — take the whole thing
        street_number = tokens[0]
        pos = 1
    else:
        # No leading number — return the whole thing as street_name
        # Try to parse suffix from the end
        result = {**empty_result}
        result["street_name"] = cleaned

        # Check if last token is a suffix
        if len(tokens) >= 2 and tokens[-1].upper() in STREET_SUFFIXES:
            result["street_suffix"] = STREET_SUFFIXES[tokens[-1].upper()]
            result["street_name"] = " ".join(tokens[:-1])

        return result

    if pos >= len(tokens):
        # Number only, nothing else
        result = {**empty_result}
        result["street_number"] = street_number
        return result

    # ---- Step 2: Route detection ----
    remaining = " ".join(tokens[pos:])
    route_match = _match_route(remaining)
    if route_match:
        route_name, post_dir = route_match
        result = {**empty_result}
        result["street_number"] = street_number
        result["street_name"] = route_name
        result["post_direction"] = post_dir
        return result

    # ---- Step 3: Pre-directional ----
    pre_direction = ""
    if pos < len(tokens):
        candidate = tokens[pos].upper()
        if candidate in DIRECTIONALS and len(candidate) <= 2:
            # Lookahead: if the NEXT token is a suffix and there's nothing
            # meaningful after it, then this "direction" is actually the street
            # name. Example: "401 N St" — "N" is the street name, not direction.
            if pos + 1 < len(tokens) and tokens[pos + 1].upper() in STREET_SUFFIXES:
                # Check if there's at most one more token after the suffix
                # (could be a post-directional)
                remaining_after_suffix = tokens[pos + 2 :]
                if len(remaining_after_suffix) <= 1:
                    # "N St" or "N St E" — N is the name, not direction
                    pass  # Don't consume as direction
                else:
                    pre_direction = DIRECTION_NORMALIZE[candidate]
                    pos += 1
            else:
                pre_direction = DIRECTION_NORMALIZE[candidate]
                pos += 1

    if pos >= len(tokens):
        result = {**empty_result}
        result["street_number"] = street_number
        result["pre_direction"] = pre_direction
        return result

    # ---- Step 4: Via detection ----
    street_name = ""
    if tokens[pos].lower() == "via":
        name_tokens = ["Via"]
        pos += 1
        while pos < len(tokens) and tokens[pos].upper() not in STREET_SUFFIXES:
            name_tokens.append(tokens[pos])
            pos += 1
        street_name = " ".join(name_tokens)
    else:
        # ---- Step 5: Consume street name tokens ----
        name_tokens = []
        while pos < len(tokens):
            upper = tokens[pos].upper()

            # Check if this token is a suffix
            if upper in STREET_SUFFIXES:
                # Special case: "St" might mean "Saint" not "Street"
                # Rule: if "St" is followed by a word and there's another
                # suffix later, treat it as "Saint"
                if upper == "ST" and pos + 1 < len(tokens):
                    future_tokens = tokens[pos + 1 :]
                    has_later_suffix = any(t.upper() in STREET_SUFFIXES for t in future_tokens)
                    if has_later_suffix:
                        # "St Joseph Dr" — St is "Saint", keep going
                        name_tokens.append(tokens[pos])
                        pos += 1
                        continue
                break  # This token is the street suffix

            # Check if this is a post-directional at the end
            if upper in DIRECTIONALS and len(upper) <= 2 and pos == len(tokens) - 1:
                break  # Last token is a post-directional

            # Check if this is a unit designator
            if upper in UNIT_TYPES:
                break

            name_tokens.append(tokens[pos])
            pos += 1

        street_name = " ".join(name_tokens)

    # ---- Step 6: Street suffix ----
    street_suffix = ""
    if pos < len(tokens) and tokens[pos].upper() in STREET_SUFFIXES:
        street_suffix = STREET_SUFFIXES[tokens[pos].upper()]
        pos += 1

    # ---- Step 7: Post-directional ----
    post_direction = ""
    if pos < len(tokens):
        candidate = tokens[pos].upper()
        if candidate in DIRECTIONALS and len(candidate) <= 2:
            post_direction = DIRECTION_NORMALIZE[candidate]
            pos += 1

    # ---- Step 8: Unit info ----
    unit_type = ""
    unit_number = ""
    if pos < len(tokens):
        candidate = tokens[pos].upper()
        if candidate in UNIT_TYPES:
            unit_type = UNIT_TYPES[candidate]
            pos += 1
            if pos < len(tokens):
                unit_number = tokens[pos]
                pos += 1
        elif candidate == "#" or (candidate.startswith("#") and len(candidate) > 1):
            # Handle "#5" as unit_type="#", unit_number="5"
            if len(candidate) > 1:
                unit_type = "#"
                unit_number = tokens[pos][1:]  # Everything after #
            else:
                unit_type = "#"
                if pos + 1 < len(tokens):
                    pos += 1
                    unit_number = tokens[pos]
            pos += 1

    return {
        "street_number": street_number,
        "pre_direction": pre_direction,
        "street_name": street_name,
        "street_suffix": street_suffix,
        "post_direction": post_direction,
        "unit_type": unit_type,
        "unit_number": unit_number,
    }


def normalize_state(state_region: str) -> str:
    """
    Convert a state name or abbreviation to a 2-letter USPS code.

    Handles both full names ("Ohio" -> "OH") and abbreviations ("OH" -> "OH").

    Args:
        state_region: State name or abbreviation from CatholicIndex data.

    Returns:
        2-letter USPS state code, or empty string if not recognized.
    """
    if not state_region:
        return ""

    state_region = state_region.strip()

    # Check full name first
    code = STATE_NAME_TO_CODE.get(state_region)
    if code:
        return code

    # Check if it's already a valid 2-letter code
    upper = state_region.upper()
    if upper in VALID_STATE_CODES:
        return upper

    # Try title case (handles lowercase input like "ohio")
    title = state_region.title()
    code = STATE_NAME_TO_CODE.get(title)
    if code:
        return code

    logger.warning(f"Unknown state: '{state_region}'")
    return state_region


def split_postal_code(postal_code: str) -> tuple[str, str]:
    """
    Split a postal code into ZIP5 and ZIP+4 components.

    Handles:
        "45810-1122" -> ("45810", "1122")
        "43901"      -> ("43901", "")
        " 43912"     -> ("43912", "")   (leading whitespace)
        None         -> ("", "")

    Args:
        postal_code: Raw postal code string from CatholicIndex.

    Returns:
        Tuple of (zip5, zip4). zip4 is empty string if not present.
    """
    if not postal_code:
        return ("", "")

    cleaned = postal_code.strip()

    if "-" in cleaned:
        parts = cleaned.split("-", 1)
        return (parts[0].strip(), parts[1].strip())

    return (cleaned, "")


def parse_church_address(church: dict) -> dict:
    """
    Parse all address fields from a church detail dict into a flat row.

    This is the main function that combines street parsing, state normalization,
    and ZIP splitting into a single output dict ready for CSV.

    Args:
        church: Dict with CatholicIndex fields: slug, name, street, city,
                stateRegion, postalCode, latitude, longitude, phone.

    Returns:
        Dict with columns:
            slug, name, street_number, pre_direction, street_name,
            street_suffix, post_direction, unit_type, unit_number,
            full_street, city, state_code, zip5, zip4,
            latitude, longitude, phone
    """
    # Parse the street address into components
    street_parts = parse_street(church.get("street", ""))

    # Normalize state to 2-letter code
    state_code = normalize_state(church.get("stateRegion", ""))

    # Split postal code into ZIP5 and ZIP+4
    zip5, zip4 = split_postal_code(church.get("postalCode", ""))

    return {
        "slug": church.get("slug", ""),
        "name": church.get("name", ""),
        "street_number": street_parts["street_number"],
        "pre_direction": street_parts["pre_direction"],
        "street_name": street_parts["street_name"],
        "street_suffix": street_parts["street_suffix"],
        "post_direction": street_parts["post_direction"],
        "unit_type": street_parts["unit_type"],
        "unit_number": street_parts["unit_number"],
        "full_street": church.get("street", ""),
        "city": church.get("city", ""),
        "state_code": state_code,
        "zip5": zip5,
        "zip4": zip4,
        "latitude": church.get("latitude", ""),
        "longitude": church.get("longitude", ""),
        "phone": church.get("phone", ""),
    }
