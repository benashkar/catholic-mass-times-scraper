"""
run_parse_addresses.py

Parse church addresses from JSONL into a structured CSV with segmented fields.

HOW TO RUN:
    python run_parse_addresses.py ohio          # Parse Ohio church addresses
    python run_parse_addresses.py texas         # Parse Texas church addresses
    python run_parse_addresses.py all           # Parse all states that have been scraped

OUTPUT:
    data/output/{state}/parsed_addresses.csv

COLUMNS:
    slug, name, street_number, pre_direction, street_name, street_suffix,
    post_direction, unit_type, unit_number, full_street, city, state_code,
    zip5, zip4, latitude, longitude, phone

REQUIRES:
    A completed statewide scrape (run_statewide.py) for the target state.
    Reads from data/output/{state}/church_details.jsonl.
"""

import sys
import json
import argparse
from pathlib import Path

# Add project root to path so imports work when running this script directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUT_DIR
from src.parsers.address_parser import parse_church_address
from src.utils.file_io import save_to_csv
from src.utils.logger import get_logger

logger = get_logger(__name__)

# State aliases — maps input to (state_code, dir_name)
# dir_name matches how run_statewide.py names its output directories
STATE_ALIASES = {
    "alabama": ("AL", "alabama"), "al": ("AL", "alabama"),
    "alaska": ("AK", "alaska"), "ak": ("AK", "alaska"),
    "arizona": ("AZ", "arizona"), "az": ("AZ", "arizona"),
    "arkansas": ("AR", "arkansas"), "ar": ("AR", "arkansas"),
    "california": ("CA", "california"), "ca": ("CA", "california"),
    "colorado": ("CO", "colorado"), "co": ("CO", "colorado"),
    "connecticut": ("CT", "connecticut"), "ct": ("CT", "connecticut"),
    "delaware": ("DE", "delaware"), "de": ("DE", "delaware"),
    "florida": ("FL", "florida"), "fl": ("FL", "florida"),
    "georgia": ("GA", "georgia"), "ga": ("GA", "georgia"),
    "hawaii": ("HI", "hawaii"), "hi": ("HI", "hawaii"),
    "idaho": ("ID", "idaho"), "id": ("ID", "idaho"),
    "illinois": ("IL", "illinois"), "il": ("IL", "illinois"),
    "indiana": ("IN", "indiana"), "in": ("IN", "indiana"),
    "iowa": ("IA", "iowa"), "ia": ("IA", "iowa"),
    "kansas": ("KS", "kansas"), "ks": ("KS", "kansas"),
    "kentucky": ("KY", "kentucky"), "ky": ("KY", "kentucky"),
    "louisiana": ("LA", "louisiana"), "la": ("LA", "louisiana"),
    "maine": ("ME", "maine"), "me": ("ME", "maine"),
    "maryland": ("MD", "maryland"), "md": ("MD", "maryland"),
    "massachusetts": ("MA", "massachusetts"), "ma": ("MA", "massachusetts"),
    "michigan": ("MI", "michigan"), "mi": ("MI", "michigan"),
    "minnesota": ("MN", "minnesota"), "mn": ("MN", "minnesota"),
    "mississippi": ("MS", "mississippi"), "ms": ("MS", "mississippi"),
    "missouri": ("MO", "missouri"), "mo": ("MO", "missouri"),
    "montana": ("MT", "montana"), "mt": ("MT", "montana"),
    "nebraska": ("NE", "nebraska"), "ne": ("NE", "nebraska"),
    "nevada": ("NV", "nevada"), "nv": ("NV", "nevada"),
    "new_hampshire": ("NH", "new_hampshire"), "nh": ("NH", "new_hampshire"),
    "new_jersey": ("NJ", "new_jersey"), "nj": ("NJ", "new_jersey"),
    "new_mexico": ("NM", "new_mexico"), "nm": ("NM", "new_mexico"),
    "new_york": ("NY", "new_york"), "ny": ("NY", "new_york"),
    "north_carolina": ("NC", "north_carolina"), "nc": ("NC", "north_carolina"),
    "north_dakota": ("ND", "north_dakota"), "nd": ("ND", "north_dakota"),
    "ohio": ("OH", "ohio"), "oh": ("OH", "ohio"),
    "oklahoma": ("OK", "oklahoma"), "ok": ("OK", "oklahoma"),
    "oregon": ("OR", "oregon"), "or": ("OR", "oregon"),
    "pennsylvania": ("PA", "pennsylvania"), "pa": ("PA", "pennsylvania"),
    "rhode_island": ("RI", "rhode_island"), "ri": ("RI", "rhode_island"),
    "south_carolina": ("SC", "south_carolina"), "sc": ("SC", "south_carolina"),
    "south_dakota": ("SD", "south_dakota"), "sd": ("SD", "south_dakota"),
    "tennessee": ("TN", "tennessee"), "tn": ("TN", "tennessee"),
    "texas": ("TX", "texas"), "tx": ("TX", "texas"),
    "utah": ("UT", "utah"), "ut": ("UT", "utah"),
    "vermont": ("VT", "vermont"), "vt": ("VT", "vermont"),
    "virginia": ("VA", "virginia"), "va": ("VA", "virginia"),
    "washington": ("WA", "washington"), "wa": ("WA", "washington"),
    "west_virginia": ("WV", "west_virginia"), "wv": ("WV", "west_virginia"),
    "wisconsin": ("WI", "wisconsin"), "wi": ("WI", "wisconsin"),
    "wyoming": ("WY", "wyoming"), "wy": ("WY", "wyoming"),
    "dc": ("DC", "dc"),
}


def resolve_state(name: str) -> tuple[str, str] | None:
    """
    Convert a state name/abbreviation to (state_code, dir_name).

    Returns:
        Tuple of (2-letter code, directory name) or None if not recognized.
    """
    key = name.lower().replace(" ", "_")
    return STATE_ALIASES.get(key)


def parse_state_addresses(state_code: str, dir_name: str) -> list[dict]:
    """
    Parse all church addresses for a given state from its JSONL file.

    Args:
        state_code: 2-letter state code (e.g., "OH")
        dir_name: Directory name used by run_statewide.py (e.g., "ohio")

    Returns:
        List of parsed address dicts ready for CSV.
    """
    jsonl_path = OUTPUT_DIR / dir_name / "church_details.jsonl"

    if not jsonl_path.exists():
        logger.error(f"No JSONL file found for {state_code}: {jsonl_path}")
        logger.error(f"Run 'python run_statewide.py {dir_name}' first.")
        return []

    logger.info(f"Parsing addresses from: {jsonl_path}")

    parsed_rows = []
    errors = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                detail = json.loads(line)
                church = detail.get("church", {})
                parsed = parse_church_address(church)
                parsed_rows.append(parsed)
            except (json.JSONDecodeError, Exception) as e:
                errors += 1
                logger.warning(f"Error parsing line {line_num}: {e}")

    if errors:
        logger.warning(f"{errors} errors encountered during parsing")

    return parsed_rows


def run_parse(states: list[str]):
    """Parse addresses for one or more states."""
    for state_input in states:
        if state_input.lower() == "all":
            # Find all states that have JSONL files
            found_dirs = []
            for d in sorted(OUTPUT_DIR.iterdir()):
                if d.is_dir() and (d / "church_details.jsonl").exists():
                    found_dirs.append(d.name)
            if not found_dirs:
                logger.error("No scraped states found. Run run_statewide.py first.")
                return
            logger.info(f"Found {len(found_dirs)} scraped states: {', '.join(found_dirs)}")
            for dir_name in found_dirs:
                _parse_and_save(dir_name.upper(), dir_name)
            return

        resolved = resolve_state(state_input)
        if not resolved:
            logger.error(f"Unknown state: '{state_input}'")
            continue
        state_code, dir_name = resolved
        _parse_and_save(state_code, dir_name)


def _parse_and_save(state_code: str, dir_name: str):
    """Parse addresses for a single state and save to CSV."""
    parsed_rows = parse_state_addresses(state_code, dir_name)
    if not parsed_rows:
        return

    # Sort by city, then name
    parsed_rows.sort(key=lambda r: (r.get("city") or "", r.get("name") or ""))

    # Save CSV
    output_path = OUTPUT_DIR / dir_name / "parsed_addresses.csv"
    save_to_csv(parsed_rows, output_path)

    # Print summary
    total = len(parsed_rows)
    with_number = sum(1 for r in parsed_rows if r["street_number"])
    with_suffix = sum(1 for r in parsed_rows if r["street_suffix"])
    with_pre_dir = sum(1 for r in parsed_rows if r["pre_direction"])
    with_post_dir = sum(1 for r in parsed_rows if r["post_direction"])
    with_zip = sum(1 for r in parsed_rows if r["zip5"])

    logger.info(f"\n{'=' * 60}")
    logger.info(f"ADDRESS PARSING COMPLETE: {state_code}")
    logger.info(f"Total addresses parsed: {total}")
    logger.info(f"  With street number: {with_number} ({100*with_number//total}%)")
    logger.info(f"  With street suffix: {with_suffix} ({100*with_suffix//total}%)")
    logger.info(f"  With pre-directional: {with_pre_dir} ({100*with_pre_dir//total}%)")
    logger.info(f"  With post-directional: {with_post_dir} ({100*with_post_dir//total}%)")
    logger.info(f"  With ZIP code: {with_zip} ({100*with_zip//total}%)")
    logger.info(f"Output: {output_path}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse church addresses from JSONL into segmented CSV."
    )
    parser.add_argument(
        "states", nargs="+",
        help="States to parse (e.g., ohio texas). Use 'all' for all scraped states."
    )
    args = parser.parse_args()
    run_parse(args.states)
