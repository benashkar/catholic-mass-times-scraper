"""Generate clergy_activity.csv from bulletin_names.csv files across all states.

Filters to high-confidence, properly-named clergy members with religious
titles or roles. Tracks activity by pdf_date to determine who is active
in 2024, 2025, 2026.
"""

import csv
import os
import re
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "output")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "clergy_activity.csv")

CLERGY_TITLES = {
    "fr.",
    "rev.",
    "deacon",
    "father",
    "msgr.",
    "bishop",
    "archbishop",
    "reverend",
    "dcn.",
    "monsignor",
    "br.",
    "brother",
    "sr.",
    "sister",
    "fr",
    "rev",
    "dcn",
}

CLERGY_ROLES = {
    "priest",
    "deacon",
    "pastor",
    "bishop",
    "monsignor",
    "archbishop",
    "brother",
    "sister",
}

JUNK_WORDS = {
    "the",
    "and",
    "for",
    "our",
    "all",
    "new",
    "holy",
    "parish",
    "church",
    "mass",
    "god",
    "lord",
    "saint",
    "prayer",
    "office",
    "team",
    "box",
    "please",
    "pastor",
    "associate",
    "important",
    "additional",
    "meeting",
    "attention",
    "bulletin",
    "announcement",
    "christmas",
    "easter",
    "advent",
    "lenten",
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "january",
    "february",
    "march",
    "abril",
    "mayo",
    "aviso",
    "importante",
    "assistance",
    "appreciation",
    "address",
    "alert",
    "angel",
    "update",
    "notice",
    "welcome",
    "special",
    "general",
    "thank",
    "information",
    "schedule",
    "calendar",
    "event",
    "registration",
    "sacrament",
    "communion",
    "baptism",
    "confirmation",
}

ROLE_PRIORITY = [
    "bishop",
    "archbishop",
    "monsignor",
    "pastor",
    "priest",
    "deacon",
    "sister",
    "brother",
]


def _load_ssa_census():
    """Load SSA first-name and Census surname sets for name validation."""
    ref_dir = os.path.join(os.path.dirname(__file__), "..", "data", "reference")
    ssa = set()
    census = set()
    ssa_path = os.path.join(ref_dir, "ssa_first_names.csv")
    census_path = os.path.join(ref_dir, "census_surnames.csv")
    if os.path.isfile(ssa_path):
        with open(ssa_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip().upper()
                if name:
                    ssa.add(name)
    if os.path.isfile(census_path):
        with open(census_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip().upper()
                if name:
                    census.add(name)
    return ssa, census


# Module-level cache
_ssa_cache = None
_census_cache = None


def _get_ref_data():
    global _ssa_cache, _census_cache
    if _ssa_cache is None:
        _ssa_cache, _census_cache = _load_ssa_census()
    return _ssa_cache, _census_cache


def is_allcaps_real_name(first, last):
    """Check if an all-caps name is a real person via SSA/Census lookup.

    Returns True if first is an SSA name AND last is a Census surname,
    meaning this is a real name that just happened to be printed in all-caps
    in a bulletin (e.g., NARESH GALI, FRANKLIN OPARA).
    """
    ssa, census = _get_ref_data()
    return first.upper() in ssa and last.upper() in census


def titlecase_name(person):
    """Convert an all-caps person name to title case, preserving title prefixes."""
    parts = person.split()
    title_prefixes = {"FR.", "REV.", "DCN.", "MSGR.", "SR.", "BR."}
    result = []
    for p in parts:
        if p.upper() in title_prefixes:
            # Standard title prefix casing
            result.append(p.capitalize() if len(p) > 3 else p[0].upper() + p[1:].lower())
        else:
            result.append(p.capitalize())
    return " ".join(result)


def is_plausible_name(person, first, last):
    """Filter out obvious non-names.

    All-caps names are rejected UNLESS they pass SSA/Census validation,
    in which case they are real names from bulletins with all-caps formatting.
    """
    # Must have first and last name with 2+ chars each
    if not last or len(last) < 2:
        return False
    if not first or len(first) < 2:
        return False
    # No numbers in name parts
    if re.search(r"\d", first + last):
        return False
    # Filter common non-name words
    if first.lower() in JUNK_WORDS or last.lower() in JUNK_WORDS:
        return False
    # All uppercase check — reject section headers, but rescue real names
    name_part = re.sub(
        r"^(Fr\.|Rev\.|Deacon|Father|Msgr\.|Sr\.|Sister|Bishop|Dcn\.|Br\.)\s*",
        "",
        person,
        flags=re.IGNORECASE,
    ).strip()
    if name_part == name_part.upper() and len(name_part) > 3:
        # All caps — only keep if SSA first + Census last validates it
        if not is_allcaps_real_name(first, last):
            return False
    return True


def get_primary_role(roles):
    """Pick the highest-ranking role from a set of roles."""
    for rp in ROLE_PRIORITY:
        for r in roles:
            if r.lower() == rp:
                return r
    return sorted(roles)[0] if roles else ""


def main():
    clergy = defaultdict(
        lambda: {
            "titles": set(),
            "roles": set(),
            "churches": set(),
            "church_names": set(),
            "cities": set(),
            "states": set(),
            "dates": set(),
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "pdf_count": 0,
        }
    )

    skipped = 0
    data_dir = os.path.abspath(DATA_DIR)

    for state_dir in sorted(os.listdir(data_dir)):
        state_path = os.path.join(data_dir, state_dir, "bulletin_names.csv")
        if not os.path.isfile(state_path):
            continue
        with open(state_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat = (row.get("category") or "").strip()
                if cat != "clergy_staff":
                    continue

                title = (row.get("title") or "").strip()
                role = (row.get("role") or "").strip()
                conf = (row.get("confidence") or "").strip()

                # Must have a clergy title or clergy role
                if title.lower() not in CLERGY_TITLES and role.lower() not in CLERGY_ROLES:
                    continue
                # High confidence only
                if conf != "high":
                    continue

                person = (row.get("person_name") or "").strip()
                first = (row.get("first_name") or "").strip()
                last = (row.get("last_name") or "").strip()
                middle = (row.get("middle_name") or "").strip()

                if not person:
                    continue
                if not is_plausible_name(person, first, last):
                    skipped += 1
                    continue

                # Rescue all-caps real names by title-casing them
                name_part = re.sub(
                    r"^(Fr\.|Rev\.|Deacon|Father|Msgr\.|Sr\.|Sister|Bishop|Dcn\.|Br\.)\s*",
                    "",
                    person,
                    flags=re.IGNORECASE,
                ).strip()
                if name_part == name_part.upper() and len(name_part) > 3:
                    person = titlecase_name(person)
                    first = first.capitalize() if first else first
                    middle = middle.capitalize() if middle else middle
                    last = last.capitalize() if last else last

                c = clergy[person]
                if title:
                    c["titles"].add(title)
                if role:
                    c["roles"].add(role)

                church_slug = (row.get("church_slug") or "").strip()
                church_name = (row.get("church_name") or "").strip()
                city = (row.get("city") or "").strip()
                pdf_date = (row.get("pdf_date") or "").strip()

                if church_slug:
                    c["churches"].add(church_slug)
                if church_name:
                    c["church_names"].add(church_name)
                if city:
                    c["cities"].add(city)
                c["states"].add(state_dir)
                # Filter out obviously bad dates
                if pdf_date and "2000" <= pdf_date <= "2027":
                    c["dates"].add(pdf_date)
                if first and not c["first_name"]:
                    c["first_name"] = first
                if middle and not c["middle_name"]:
                    c["middle_name"] = middle
                if last and not c["last_name"]:
                    c["last_name"] = last
                c["pdf_count"] += 1

    # Write output CSV
    fields = [
        "person_name",
        "title",
        "first_name",
        "middle_name",
        "last_name",
        "primary_role",
        "all_roles",
        "church_count",
        "churches",
        "church_names",
        "city_count",
        "cities",
        "states",
        "pdf_count",
        "bulletin_count_with_date",
        "earliest_bulletin",
        "latest_bulletin",
        "active_2026",
        "active_2025",
        "active_2024",
        "years_active",
        "confidence",
    ]

    output_path = os.path.abspath(OUTPUT_PATH)
    rows_written = 0

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for person in sorted(clergy.keys()):
            c = clergy[person]
            dates = sorted(c["dates"])
            years = sorted(set(d[:4] for d in dates))

            writer.writerow(
                {
                    "person_name": person,
                    "title": "; ".join(sorted(c["titles"])),
                    "first_name": c["first_name"],
                    "middle_name": c["middle_name"],
                    "last_name": c["last_name"],
                    "primary_role": get_primary_role(c["roles"]),
                    "all_roles": "; ".join(sorted(c["roles"])),
                    "church_count": len(c["churches"]),
                    "churches": "; ".join(sorted(c["churches"])),
                    "church_names": "; ".join(sorted(c["church_names"])),
                    "city_count": len(c["cities"]),
                    "cities": "; ".join(sorted(c["cities"])),
                    "states": "; ".join(sorted(c["states"])),
                    "pdf_count": c["pdf_count"],
                    "bulletin_count_with_date": len(dates),
                    "earliest_bulletin": dates[0] if dates else "",
                    "latest_bulletin": dates[-1] if dates else "",
                    "active_2026": any(d.startswith("2026") for d in dates),
                    "active_2025": any(d.startswith("2025") for d in dates),
                    "active_2024": any(d.startswith("2024") for d in dates),
                    "years_active": "; ".join(years),
                    "confidence": "high",
                }
            )
            rows_written += 1

    # Print summary
    active26 = sum(1 for c in clergy.values() if any(d.startswith("2026") for d in c["dates"]))
    active25 = sum(1 for c in clergy.values() if any(d.startswith("2025") for d in c["dates"]))
    no_date = sum(1 for c in clergy.values() if not c["dates"])
    multi = sum(1 for c in clergy.values() if len(c["churches"]) > 1)

    print(f"Rows skipped (noise):   {skipped:,}")
    print(f"Wrote {rows_written:,} clergy records to {output_path}")
    print()
    print("=== Cleaned Clergy Summary ===")
    print(f"Total unique clergy:              {rows_written:>8,}")
    print(f"Active in 2026:                   {active26:>8,}")
    print(f"Active in 2025:                   {active25:>8,}")
    print(f"No dated bulletins:               {no_date:>8,}")
    print(f"Appearing at 2+ churches:         {multi:>8,}")


if __name__ == "__main__":
    main()
