"""
transformers.py

Maps CatholicIndex raw data values to our normalized database lookup codes.

WHY THIS EXISTS:
    CatholicIndex uses its own naming conventions (e.g., "Confession" with a
    capital C, "first_friday_(recurring)" with parentheses). Our database uses
    cleaner, shorter codes. This module is the single source of truth for all
    those mappings.

HOW IT WORKS:
    Each mapping is a plain dictionary: raw value -> database code.
    The transform_*() functions apply these mappings and handle edge cases
    (nulls, unknown values, etc.).

USAGE:
    from src.etl.transformers import transform_service, transform_church

    # Transform a single service dict from CatholicIndex format to DB format
    db_row = transform_service(raw_service_dict, church_id=42)

    # Transform a church dict from CatholicIndex format to DB format
    db_row = transform_church(raw_church_dict)
"""

import re
from datetime import UTC, datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# RAW VALUE -> DATABASE CODE MAPPINGS
# =============================================================================

# CatholicIndex category name -> our category_code
CATEGORY_MAP = {
    "Mass": "mass",
    "Confession": "confession",
    "Adoration": "adoration",
    "Devotions": "devotions",
    "Education": "education",
    "Community": "community",
    "Other": "other",
}

# CatholicIndex schedule type -> our schedule_type_code
# These happen to be the same, but we map explicitly for clarity and safety.
SCHEDULE_TYPE_MAP = {
    "sunday": "sunday",
    "saturday": "saturday",
    "weekday": "weekday",
    "specific_weekday": "specific_weekday",
    "monthly": "monthly",
    "special_event": "special_event",
    "parish_event": "parish_event",
    "other": "other",
}

# CatholicIndex language string -> our language_code
# NULL in CatholicIndex means English (the default), which we store as NULL too.
LANGUAGE_MAP = {
    "english": "en",
    "spanish": "es",
    "latin": "la",
    "bilingual": "bi",
    "vietnamese": "vi",
    "korean": "ko",
    "polish": "pl",
    "portuguese": "pt",
}

# CatholicIndex recurrence pattern -> our pattern_code
# CatholicIndex uses verbose names with parentheses; we use clean codes.
PATTERN_MAP = {
    "first_friday_(recurring)": "first_friday",
    "first_saturday_(recurring)": "first_saturday",
    "first_sunday": "first_sunday",
    "thursday_(before_first_friday)": "thursday_before_first_friday",
    "third_sunday": "third_sunday",
    "last_wednesday": "last_wednesday",
}

# CatholicIndex time relation -> our relation_code
# These happen to be the same, mapped explicitly for safety.
TIME_RELATION_MAP = {
    "before": "before",
    "after": "after",
}

# Full state name -> 2-letter USPS code
# CatholicIndex uses full state names like "Ohio"; our DB uses "OH".
STATE_MAP = {
    "Ohio": "OH",
    "Pennsylvania": "PA",
    "Indiana": "IN",
    "Kentucky": "KY",
    "West Virginia": "WV",
    "Michigan": "MI",
    "Illinois": "IL",
    "Virginia": "VA",
    "New York": "NY",
    "New Jersey": "NJ",
    "Maryland": "MD",
    "Tennessee": "TN",
    "Georgia": "GA",
    "Florida": "FL",
    "Texas": "TX",
    "California": "CA",
    "Massachusetts": "MA",
    "Connecticut": "CT",
    "Wisconsin": "WI",
    "Minnesota": "MN",
    "Iowa": "IA",
    "Missouri": "MO",
    "Louisiana": "LA",
    "Alabama": "AL",
    "Mississippi": "MS",
    "Arkansas": "AR",
    "Oklahoma": "OK",
    "Kansas": "KS",
    "Nebraska": "NE",
    "South Dakota": "SD",
    "North Dakota": "ND",
    "Montana": "MT",
    "Wyoming": "WY",
    "Colorado": "CO",
    "New Mexico": "NM",
    "Arizona": "AZ",
    "Utah": "UT",
    "Nevada": "NV",
    "Idaho": "ID",
    "Oregon": "OR",
    "Washington": "WA",
    "North Carolina": "NC",
    "South Carolina": "SC",
    "Maine": "ME",
    "New Hampshire": "NH",
    "Vermont": "VT",
    "Rhode Island": "RI",
    "Delaware": "DE",
    "Hawaii": "HI",
    "Alaska": "AK",
    # DC is not a state but has Catholic churches
    "District of Columbia": "DC",
}


# =============================================================================
# NOTE PARSING — Extract structured tags from free-text notes
# =============================================================================

# Each rule is a tuple: (tag_code, regex_pattern)
# The regex is applied case-insensitively to the notes_raw string.
# If it matches, the tag_code is added to the service's note tags.
NOTE_TAG_RULES = [
    ("vigil", r"\bvigil\b"),
    ("by_appointment", r"\bby\s+appointment\b"),
    ("24_hours", r"\b24\s*hours?\b"),
    ("school_mass", r"\bschool\s+(mass|liturgy)\b"),
    ("holy_day", r"\bholy\s+day\b"),
    ("bilingual", r"\bbilingual\b"),
    ("exposition", r"\bexposition\b"),
    ("benediction", r"\bbenediction\b"),
    ("livestreamed", r"\blivestrea?m"),
    ("seasonal", r"\b(lent|advent|christmas|easter)\b"),
    ("see_bulletin", r"\b(see|check)\s+(the\s+)?bulletin\b"),
    ("public_welcome", r"\bpublic\s+(is\s+)?welcome\b"),
    ("first_friday", r"\bfirst\s+friday\b"),
    ("first_saturday", r"\bfirst\s+saturday\b"),
    ("all_day", r"\ball\s+day\b"),
]


def parse_note_tags(notes_raw: str | None) -> list[str]:
    """
    Extract structured tags from a free-text notes string.

    Applies regex rules to identify known patterns in the notes field
    and returns a list of tag codes that matched.

    Args:
        notes_raw: The raw notes string from CatholicIndex. May be None.

    Returns:
        A list of tag_code strings that matched (e.g., ["vigil", "by_appointment"]).
        Returns an empty list if notes_raw is None or no patterns matched.

    Example:
        >>> parse_note_tags("vigil; Or anytime by appointment")
        ['vigil', 'by_appointment']
        >>> parse_note_tags(None)
        []
    """
    if not notes_raw:
        return []

    tags = []
    for tag_code, pattern in NOTE_TAG_RULES:
        if re.search(pattern, notes_raw, re.IGNORECASE):
            tags.append(tag_code)

    return tags


# =============================================================================
# TRANSFORM FUNCTIONS
# =============================================================================


def transform_church(raw: dict, diocese_id: int | None = None) -> dict:
    """
    Transform a CatholicIndex church dict into our database column format.

    This handles:
      - Mapping state names to 2-letter codes
      - Extracting boolean flags (has_livestream, has_perpetual_adoration)
      - Cleaning up website URLs (removing CatholicIndex redirect wrappers)
      - Parsing the updatedAt timestamp

    Args:
        raw: A church dict from CatholicIndex (either from discovery or detail page).
             Expected keys: slug, name, street, city, stateRegion, postalCode,
             latitude, longitude, phone, website, livestreamUrl, updatedAt,
             hasPerpetualAdoration, massCount, confessionCount, adorationCount,
             streamCount
        diocese_id: Optional diocese ID to assign to this church.

    Returns:
        A dict with keys matching our database column names, ready for INSERT.

    Example:
        >>> raw = {"slug": "us-oh-grove-city-...", "name": "Our Lady", "stateRegion": "Ohio", ...}
        >>> transform_church(raw)
        {"slug": "us-oh-grove-city-...", "name": "Our Lady", "state_code": "OH", ...}
    """
    state_name = raw.get("stateRegion", "")
    state_code = STATE_MAP.get(state_name, state_name[:2].upper() if state_name else None)

    if state_name and state_name not in STATE_MAP:
        logger.warning(
            f"Unknown state '{state_name}' for church {raw.get('name')} — using '{state_code}'"
        )

    # Parse the updatedAt timestamp from ISO 8601 format
    updated_at = None
    if raw.get("updatedAt"):
        try:
            updated_at = datetime.fromisoformat(raw["updatedAt"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning(
                f"Could not parse updatedAt '{raw.get('updatedAt')}' for {raw.get('name')}"
            )

    # Website URL: CatholicIndex wraps it in a redirect (/api/out?id=...&type=website&...)
    # We store the wrapped URL as-is for now; we could resolve it later.
    website_url = raw.get("website")
    livestream_url = raw.get("livestreamUrl")

    return {
        "slug": raw.get("slug"),
        "name": raw.get("name"),
        "street": raw.get("street"),
        "city": raw.get("city"),
        "state_code": state_code,
        "postal_code": raw.get("postalCode"),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "phone": raw.get("phone"),
        "website_url": website_url,
        "livestream_url": livestream_url,
        "diocese_id": diocese_id,
        "has_perpetual_adoration": bool(raw.get("hasPerpetualAdoration", False)),
        "has_livestream": bool(livestream_url),
        "mass_count": raw.get("massCount", 0) or 0,
        "confession_count": raw.get("confessionCount", 0) or 0,
        "adoration_count": raw.get("adorationCount", 0) or 0,
        "stream_count": raw.get("streamCount", 0) or 0,
        "schedule_updated_at": updated_at,
        "source_url": raw.get("source_url"),
        "community_insights": raw.get("communityInsights"),
        "last_scraped_at": datetime.now(UTC),
    }


def transform_service(raw: dict, church_id: int) -> dict:
    """
    Transform a CatholicIndex service dict into our database column format.

    This handles:
      - Mapping category, schedule type, language, pattern, time relation to codes
      - Keeping the raw notes string as notes_raw (for audit)
      - Setting church_id for the FK relationship

    Args:
        raw: A service dict from CatholicIndex. Expected keys: serviceId, category,
             scheduleType, dayOfWeek, timeStart, timeEnd, displayName, language,
             location, eventDate, pattern, timeRelation, referenceService,
             offsetMinutes, notes
        church_id: The database church_id this service belongs to.

    Returns:
        A dict with keys matching our database column names, ready for INSERT.
    """
    # Map category
    category = raw.get("category", "Other")
    category_code = CATEGORY_MAP.get(category)
    if not category_code:
        logger.warning(f"Unknown category '{category}' — defaulting to 'other'")
        category_code = "other"

    # Map schedule type
    schedule_type = raw.get("scheduleType", "other")
    schedule_type_code = SCHEDULE_TYPE_MAP.get(schedule_type)
    if not schedule_type_code:
        logger.warning(f"Unknown schedule type '{schedule_type}' — defaulting to 'other'")
        schedule_type_code = "other"

    # Map language (None = English default, stored as NULL)
    language = raw.get("language")
    language_code = None
    if language:
        language_code = LANGUAGE_MAP.get(language.lower())
        if not language_code:
            logger.warning(f"Unknown language '{language}' — storing as NULL")

    # Map recurrence pattern
    pattern = raw.get("pattern")
    pattern_code = None
    if pattern:
        pattern_code = PATTERN_MAP.get(pattern)
        if not pattern_code:
            logger.warning(f"Unknown recurrence pattern '{pattern}' — storing as NULL")

    # Map time relation
    time_relation = raw.get("timeRelation")
    relation_code = None
    if time_relation:
        relation_code = TIME_RELATION_MAP.get(time_relation)
        if not relation_code:
            logger.warning(f"Unknown time relation '{time_relation}' — storing as NULL")

    return {
        "church_id": church_id,
        "source_service_id": raw.get("serviceId"),
        "category_code": category_code,
        "schedule_type_code": schedule_type_code,
        "day_code": raw.get("dayOfWeek"),  # Already "Mon", "Tue", etc.
        "time_start": raw.get("timeStart"),  # Already "HH:MM:SS" format
        "time_end": raw.get("timeEnd"),
        "event_date": raw.get("eventDate"),  # Already "YYYY-MM-DD" format
        "language_code": language_code,
        "pattern_code": pattern_code,
        "relation_code": relation_code,
        "reference_service": raw.get("referenceService"),
        "offset_minutes": raw.get("offsetMinutes"),
        "location": raw.get("location"),
        "display_name": raw.get("displayName", "Unknown"),
        "notes_raw": raw.get("notes"),
    }


def transform_community(raw: dict) -> dict:
    """
    Transform a TARGET_COMMUNITIES config dict into our database column format.

    Args:
        raw: A community dict from config/settings.py TARGET_COMMUNITIES.
             Expected keys: name, county, state, zips, fallback_city (optional)

    Returns:
        A dict with keys matching our database community column names.
    """
    return {
        "name": raw.get("name"),
        "county": raw.get("county"),
        "state_code": raw.get("state"),  # Already 2-letter code in config
        "zip_codes": ",".join(raw.get("zips", [])),
        "fallback_city": raw.get("fallback_city"),
    }
