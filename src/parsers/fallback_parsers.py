"""
fallback_parsers.py — Secondary parsing for bulletin name fields.

These functions attempt to fill in empty fields (first_name, last_name,
category, role, title) when the primary extraction pipeline returns blanks.
All functions are pure string/regex — no HTTP, no DB.
"""

import re

from src.parsers.bulletin_constants import (
    HONORIFIC_PATTERN,
    HONORIFIC_TITLES,
    MINISTRY_ROLES,
    STAFF_ROLES,
)

# Pre-compiled patterns
_HONORIFIC_RE = re.compile(rf"^({HONORIFIC_PATTERN})\.?\s+", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\s+(?:Jr|Sr|III|IV|II)\.?$", re.IGNORECASE)

# Category keyword groups
_CATEGORY_PATTERNS = [
    (
        "prayer_list",
        re.compile(
            r"\b(?:pray(?:er)?|sick|ill\b|homebound|healing|get\s+well|health\s+of)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mass_intention",
        re.compile(
            r"\b(?:repose|soul|intention|memory|deceased|eternal\s+rest|rest\s+in\s+peace)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "clergy_staff",
        re.compile(
            r"\b(?:pastor|director|staff|ministry|deacon|coordinator|secretary|"
            r"business\s+manager|office\s+manager|principal|custodian|"
            r"bookkeeper|receptionist|organist|cantor|sacristan)\b",
            re.IGNORECASE,
        ),
    ),
]

# Role extraction pattern — looks for role keywords
_ROLE_RE = re.compile(rf"\b({STAFF_ROLES})\b", re.IGNORECASE)
_MINISTRY_ROLE_RE = re.compile(rf"\b({MINISTRY_ROLES})\b", re.IGNORECASE)


def parse_first_last_from_person_name(person_name):
    """Try harder to split person_name into first/last when primary parsing failed.

    Handles:
    - Comma-reversed: "SMITH, JOHN" or "Smith, John M."
    - Honorific stripping: "Fr. John Smith" -> first=John, last=Smith
    - Suffix handling: "John Smith Jr." -> first=John, last=Smith
    - Single-word fallback: returns as last_name

    Returns:
        dict with 'first_name' and 'last_name' (may be empty strings)
    """
    if not person_name or not person_name.strip():
        return {"first_name": "", "last_name": ""}

    name = person_name.strip()

    # Strip honorific prefix
    name = _HONORIFIC_RE.sub("", name).strip()

    # Strip suffix
    name = _SUFFIX_RE.sub("", name).strip()

    # Handle comma-reversed format: "SMITH, JOHN" or "Smith, John M."
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first_and_middle = parts[1].strip()
        # First token of the first_and_middle is the first name
        first_tokens = first_and_middle.split()
        first = first_tokens[0] if first_tokens else ""
        # Title-case if all-caps
        if last.isupper() and len(last) > 1:
            last = last.title()
        if first.isupper() and len(first) > 1:
            first = first.title()
        return {"first_name": first, "last_name": last}

    # Standard split: first [middle] last
    tokens = name.split()
    if len(tokens) == 0:
        return {"first_name": "", "last_name": ""}
    if len(tokens) == 1:
        # Single word — could be a last name
        return {"first_name": "", "last_name": tokens[0]}
    if len(tokens) == 2:
        return {"first_name": tokens[0], "last_name": tokens[1]}

    # 3+ tokens: first = first token, last = last token
    # Skip middle initial (single letter or letter with period)
    return {"first_name": tokens[0], "last_name": tokens[-1]}


def parse_category_from_context(context):
    """Infer bulletin category from the surrounding context text.

    Returns one of: 'prayer_list', 'mass_intention', 'clergy_staff', or ''
    """
    if not context:
        return ""

    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(context):
            return category

    return ""


def parse_role_from_context(context):
    """Extract a role keyword from context text.

    Looks for staff/ministry role keywords within the context. Returns the
    first match or empty string.
    """
    if not context:
        return ""

    # Try staff roles first (more specific)
    m = _ROLE_RE.search(context)
    if m:
        return m.group(1).strip()

    # Then ministry roles
    m = _MINISTRY_ROLE_RE.search(context)
    if m:
        return m.group(1).strip()

    return ""


def parse_title_from_person_name(person_name):
    """Extract honorific title from person_name if primary parsing missed it.

    Returns the honorific (e.g. 'Fr.', 'Rev.', 'Msgr.') or empty string.
    """
    if not person_name:
        return ""

    m = _HONORIFIC_RE.match(person_name.strip())
    if m:
        title = m.group(1).strip()
        # Normalize: ensure it ends with period if it's an abbreviation
        if title.lower() in ("fr", "rev", "msgr", "dcn", "sr", "br", "dr"):
            title = title.title() + "."
        return title

    # Check first word against known titles
    first_word = person_name.strip().split()[0] if person_name.strip() else ""
    if first_word.rstrip(".") in {t.rstrip(".") for t in HONORIFIC_TITLES}:
        return first_word if first_word.endswith(".") else first_word + "."

    return ""
