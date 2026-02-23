"""
run_statewide.py

Statewide church discovery and detail scraping.

Discovers every Catholic church in a state by querying CatholicIndex.org for
every city in that state, then scrapes full schedule detail for each unique
church found. Saves progress incrementally so interrupted runs can resume.

HOW TO RUN:
    python run_statewide.py ohio              # Ohio only
    python run_statewide.py texas             # Texas only
    python run_statewide.py ohio texas        # Both states (sequential)
    python run_statewide.py ohio --resume     # Resume an interrupted Ohio run
    python run_statewide.py ohio --limit 10   # Test with first 10 cities only
    python run_statewide.py ohio --discovery-only   # Only discover, skip detail scrape
    python run_statewide.py ohio --detail-only      # Only detail scrape (requires prior discovery)
    python run_statewide.py all --dates-only        # Just regenerate dated_services.csv (fast, no scraping)

EXPECTED RUNTIME (full run):
    Ohio (~1,077 cities):  ~30 min discovery + ~20 min detail = ~50 min
    Texas (~1,480 cities): ~40 min discovery + ~90 min detail = ~2 hours

OUTPUT FILES (per state):
    data/churches/{state}/master_church_list.csv      — All unique churches
    data/churches/{state}/discovery_progress.json     — Resume checkpoint
    data/output/{state}/all_churches_detail.json      — Raw JSON detail
    data/output/{state}/church_details.jsonl           — Incremental detail (for resume)
    data/output/{state}/detail_progress.json           — Resume checkpoint
    data/output/{state}/all_services.csv               — One row per service (viewable in Excel)
    data/output/{state}/dated_services.csv             — Services mapped to actual dates (next 12 weeks)

WHY INCREMENTAL SAVES:
    A full Ohio run takes ~50 minutes. If the script crashes at minute 40,
    you don't want to lose everything. The progress files track exactly which
    cities/churches are done. The JSONL file appends each church detail as it
    completes. On --resume, we skip everything already done and continue.
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import CHURCHES_DIR, OUTPUT_DIR, CITY_LISTS_DIR, PROGRESS_SAVE_INTERVAL
from src.scrapers.catholic_index import discover_churches, scrape_church_detail
from src.utils.file_io import save_to_csv, load_from_csv, save_to_json, load_from_json
from src.etl.csv_generator import generate_services_csv, generate_dated_services_csv
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# State name resolution
# ============================================================================

# Map various inputs to (state_abbrev, state_name_lower) pairs
STATE_ALIASES = {
    "ohio": ("OH", "ohio"), "oh": ("OH", "ohio"),
    "texas": ("TX", "texas"), "tx": ("TX", "texas"),
    "california": ("CA", "california"), "ca": ("CA", "california"),
    "florida": ("FL", "florida"), "fl": ("FL", "florida"),
    "new-york": ("NY", "new_york"), "ny": ("NY", "new_york"), "new york": ("NY", "new_york"),
    "pennsylvania": ("PA", "pennsylvania"), "pa": ("PA", "pennsylvania"),
    "illinois": ("IL", "illinois"), "il": ("IL", "illinois"),
    "michigan": ("MI", "michigan"), "mi": ("MI", "michigan"),
    "georgia": ("GA", "georgia"), "ga": ("GA", "georgia"),
    "north-carolina": ("NC", "north_carolina"), "nc": ("NC", "north_carolina"),
    "indiana": ("IN", "indiana"), "in": ("IN", "indiana"),
    "virginia": ("VA", "virginia"), "va": ("VA", "virginia"),
    "tennessee": ("TN", "tennessee"), "tn": ("TN", "tennessee"),
    "missouri": ("MO", "missouri"), "mo": ("MO", "missouri"),
    "maryland": ("MD", "maryland"), "md": ("MD", "maryland"),
    "wisconsin": ("WI", "wisconsin"), "wi": ("WI", "wisconsin"),
    "minnesota": ("MN", "minnesota"), "mn": ("MN", "minnesota"),
    "colorado": ("CO", "colorado"), "co": ("CO", "colorado"),
    "alabama": ("AL", "alabama"), "al": ("AL", "alabama"),
    "south-carolina": ("SC", "south_carolina"), "sc": ("SC", "south_carolina"),
    "louisiana": ("LA", "louisiana"), "la": ("LA", "louisiana"),
    "kentucky": ("KY", "kentucky"), "ky": ("KY", "kentucky"),
    "oregon": ("OR", "oregon"), "or": ("OR", "oregon"),
    "oklahoma": ("OK", "oklahoma"), "ok": ("OK", "oklahoma"),
    "connecticut": ("CT", "connecticut"), "ct": ("CT", "connecticut"),
    "iowa": ("IA", "iowa"), "ia": ("IA", "iowa"),
    "mississippi": ("MS", "mississippi"), "ms": ("MS", "mississippi"),
    "arkansas": ("AR", "arkansas"), "ar": ("AR", "arkansas"),
    "utah": ("UT", "utah"), "ut": ("UT", "utah"),
    "kansas": ("KS", "kansas"), "ks": ("KS", "kansas"),
    "nevada": ("NV", "nevada"), "nv": ("NV", "nevada"),
    "nebraska": ("NE", "nebraska"), "ne": ("NE", "nebraska"),
    "new-mexico": ("NM", "new_mexico"), "nm": ("NM", "new_mexico"),
    "west-virginia": ("WV", "west_virginia"), "wv": ("WV", "west_virginia"),
    "idaho": ("ID", "idaho"), "id": ("ID", "idaho"),
    "hawaii": ("HI", "hawaii"), "hi": ("HI", "hawaii"),
    "new-hampshire": ("NH", "new_hampshire"), "nh": ("NH", "new_hampshire"),
    "maine": ("ME", "maine"), "me": ("ME", "maine"),
    "montana": ("MT", "montana"), "mt": ("MT", "montana"),
    "rhode-island": ("RI", "rhode_island"), "ri": ("RI", "rhode_island"),
    "delaware": ("DE", "delaware"), "de": ("DE", "delaware"),
    "south-dakota": ("SD", "south_dakota"), "sd": ("SD", "south_dakota"),
    "north-dakota": ("ND", "north_dakota"), "nd": ("ND", "north_dakota"),
    "alaska": ("AK", "alaska"), "ak": ("AK", "alaska"),
    "vermont": ("VT", "vermont"), "vt": ("VT", "vermont"),
    "wyoming": ("WY", "wyoming"), "wy": ("WY", "wyoming"),
    "arizona": ("AZ", "arizona"), "az": ("AZ", "arizona"),
    "washington": ("WA", "washington"), "wa": ("WA", "washington"),
    "massachusetts": ("MA", "massachusetts"), "ma": ("MA", "massachusetts"),
    "new-jersey": ("NJ", "new_jersey"), "nj": ("NJ", "new_jersey"),
}


def resolve_state(user_input: str) -> tuple[str, str]:
    """
    Resolve user input (e.g., "ohio", "OH", "tx") to (state_abbrev, state_name).
    Returns (state_abbrev, state_name_lowercase) or exits with error.
    """
    key = user_input.lower().strip()
    if key in STATE_ALIASES:
        return STATE_ALIASES[key]
    logger.error(f"Unknown state: '{user_input}'. Use full name or 2-letter code.")
    sys.exit(1)


# ============================================================================
# City list loading
# ============================================================================

def load_city_list(state_abbrev: str) -> list[dict]:
    """
    Load the city list for a state from the master US cities CSV.

    We filter the master us_cities.csv by state_code. This avoids needing
    a separate CSV per state — one file covers all 50 states.

    Returns:
        List of city dicts with keys: city, state_code, state_name, county, latitude, longitude
    """
    # Try state-specific file first, then fall back to master
    master_path = CITY_LISTS_DIR / "us_cities.csv"

    if not master_path.exists():
        logger.error(f"City list not found at {master_path}")
        logger.error("Run the download script or place us_cities.csv in data/city_lists/")
        sys.exit(1)

    all_cities = load_from_csv(master_path)
    state_cities = [c for c in all_cities if c.get('state_code') == state_abbrev]

    if not state_cities:
        logger.error(f"No cities found for state '{state_abbrev}' in {master_path}")
        sys.exit(1)

    logger.info(f"Loaded {len(state_cities)} cities for {state_abbrev}")
    return state_cities


# ============================================================================
# Progress tracking
# ============================================================================

def load_progress(path: Path) -> dict:
    """Load a progress file, or return empty structure."""
    if path.exists():
        data = load_from_json(path)
        if data:
            return data
    return {}


def save_progress_file(data: dict, path: Path):
    """Save progress data to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(data, path)


# ============================================================================
# Phase 1: Discovery — find all churches in a state
# ============================================================================

def run_discovery(state_abbrev: str, state_name: str, resume: bool = False, limit: int | None = None):
    """
    Discover all churches in a state by querying every city on CatholicIndex.

    Args:
        state_abbrev: 2-letter state code ("OH", "TX")
        state_name: Lowercase state name ("ohio", "texas")
        resume: If True, continue from where we left off
        limit: If set, only process the first N cities (for testing)

    Returns:
        Path to the generated master_church_list.csv
    """
    state_dir = CHURCHES_DIR / state_name
    state_dir.mkdir(parents=True, exist_ok=True)
    progress_path = state_dir / "discovery_progress.json"

    # Load city list
    cities = load_city_list(state_abbrev)
    if limit:
        cities = cities[:limit]
    total_cities = len(cities)

    # Load or create progress
    progress = load_progress(progress_path) if resume else {}
    completed_cities = set(progress.get("completed_cities", []))
    discovered_churches = progress.get("discovered_churches", {})
    stats = progress.get("stats", {"cities_with_results": 0, "cities_404": 0})

    logger.info("=" * 70)
    logger.info(f"DISCOVERY: {state_name.upper()} ({total_cities} cities)")
    if resume and completed_cities:
        logger.info(f"Resuming — {len(completed_cities)} cities already done, {len(discovered_churches)} churches found")
    logger.info("=" * 70)

    start_time = time.time()
    processed_this_run = 0

    for i, city_row in enumerate(cities, 1):
        city_name = city_row["city"]
        city_key = city_name.lower().strip()

        # Skip already-completed cities
        if city_key in completed_cities:
            continue

        # Discover churches for this city
        churches = discover_churches(city_name, state_abbrev)

        # Deduplicate: add only new churches
        new_count = 0
        for church in churches:
            slug = church.get("slug")
            if slug and slug not in discovered_churches:
                discovered_churches[slug] = {
                    "slug": slug,
                    "name": church.get("name", ""),
                    "street": church.get("street", ""),
                    "city": church.get("city", ""),
                    "state_region": church.get("stateRegion", ""),
                    "latitude": church.get("latitude", ""),
                    "longitude": church.get("longitude", ""),
                    "phone": church.get("phone", ""),
                    "website": church.get("website", ""),
                    "mass_count": church.get("massCount", 0),
                    "confession_count": church.get("confessionCount", 0),
                    "adoration_count": church.get("adorationCount", 0),
                    "has_perpetual_adoration": church.get("hasPerpetualAdoration", False),
                    "source_url": church.get("source_url", ""),
                    "last_scraped": church.get("last_scraped", ""),
                }
                new_count += 1

        # Update tracking
        if churches:
            stats["cities_with_results"] = stats.get("cities_with_results", 0) + 1
        else:
            stats["cities_404"] = stats.get("cities_404", 0) + 1

        completed_cities.add(city_key)
        processed_this_run += 1

        # Progress display with ETA
        total_done = len(completed_cities)
        unique_total = len(discovered_churches)
        elapsed = time.time() - start_time
        if processed_this_run > 0:
            rate = elapsed / processed_this_run
            remaining = total_cities - total_done
            eta_min = (remaining * rate) / 60
            logger.info(
                f"[{total_done}/{total_cities}] {city_name}: "
                f"{len(churches)} found ({new_count} new) | "
                f"Unique total: {unique_total} | "
                f"ETA: {eta_min:.0f}m"
            )

        # Periodic progress save
        if processed_this_run % PROGRESS_SAVE_INTERVAL == 0:
            save_progress_file({
                "completed_cities": list(completed_cities),
                "discovered_churches": discovered_churches,
                "stats": stats,
            }, progress_path)

    # Final save
    save_progress_file({
        "completed_cities": list(completed_cities),
        "discovered_churches": discovered_churches,
        "stats": stats,
    }, progress_path)

    # Generate master church list CSV
    master_list = sorted(
        discovered_churches.values(),
        key=lambda c: (c.get("city") or "", c.get("name") or "")
    )
    csv_path = state_dir / "master_church_list.csv"
    if master_list:
        save_to_csv(master_list, csv_path)

    elapsed_total = (time.time() - start_time) / 60
    logger.info("\n" + "=" * 70)
    logger.info(f"DISCOVERY COMPLETE: {state_name.upper()}")
    logger.info(f"Cities queried: {len(completed_cities)}/{total_cities}")
    logger.info(f"Cities with results: {stats.get('cities_with_results', 0)}")
    logger.info(f"Cities 404'd: {stats.get('cities_404', 0)}")
    logger.info(f"Unique churches: {len(master_list)}")
    logger.info(f"Time: {elapsed_total:.1f} minutes")
    logger.info(f"Saved: {csv_path}")
    logger.info("=" * 70)

    return csv_path


# ============================================================================
# Phase 2: Detail scrape — get full schedules for every church
# ============================================================================

def run_detail_scrape(state_name: str, resume: bool = False):
    """
    Scrape full schedule detail for every church in a state's master list.

    Uses JSONL (JSON Lines) for incremental saves — each church's detail is
    appended as one line as soon as it's scraped. If the script crashes,
    all previously scraped churches are safely on disk.

    Args:
        state_name: Lowercase state name ("ohio", "texas")
        resume: If True, skip already-scraped churches
    """
    state_churches_dir = CHURCHES_DIR / state_name
    state_output_dir = OUTPUT_DIR / state_name
    state_output_dir.mkdir(parents=True, exist_ok=True)

    # Load master church list
    master_path = state_churches_dir / "master_church_list.csv"
    master_list = load_from_csv(master_path)
    if not master_list:
        logger.error(f"No master church list at {master_path}")
        logger.error("Run discovery first: python run_statewide.py {state} --discovery-only")
        return

    total = len(master_list)

    # Load progress
    progress_path = state_output_dir / "detail_progress.json"
    jsonl_path = state_output_dir / "church_details.jsonl"

    completed_slugs = set()
    if resume and progress_path.exists():
        prog = load_progress(progress_path)
        completed_slugs = set(prog.get("completed_slugs", []))
        logger.info(f"Resuming — {len(completed_slugs)} churches already scraped")

    # If not resuming, clear the JSONL file
    if not resume:
        jsonl_path.unlink(missing_ok=True)

    logger.info("=" * 70)
    logger.info(f"DETAIL SCRAPE: {state_name.upper()} ({total} churches)")
    logger.info("=" * 70)

    start_time = time.time()
    success_count = len(completed_slugs)
    fail_count = 0
    processed_this_run = 0

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_file:
        for i, church in enumerate(master_list, 1):
            slug = church["slug"]
            if slug in completed_slugs:
                continue

            name = church.get("name", "?")
            city = church.get("city", "?")

            detail = scrape_church_detail(slug)
            if detail:
                jsonl_file.write(json.dumps(detail, ensure_ascii=False) + "\n")
                jsonl_file.flush()
                completed_slugs.add(slug)
                success_count += 1
                svc_count = detail.get('totalServices', 0)
            else:
                fail_count += 1
                svc_count = "FAILED"

            processed_this_run += 1

            # Progress with ETA
            elapsed = time.time() - start_time
            if processed_this_run > 0:
                rate = elapsed / processed_this_run
                remaining = total - (success_count + fail_count)
                eta_min = (remaining * rate) / 60
                logger.info(
                    f"[{success_count + fail_count}/{total}] {name} ({city}): "
                    f"{svc_count} services | ETA: {eta_min:.0f}m"
                )

            # Periodic progress save
            if processed_this_run % PROGRESS_SAVE_INTERVAL == 0:
                save_progress_file({"completed_slugs": list(completed_slugs)}, progress_path)

    # Final progress save
    save_progress_file({"completed_slugs": list(completed_slugs)}, progress_path)

    # Read all details from JSONL for CSV generation
    logger.info("\nLoading JSONL and generating output CSVs...")
    all_details = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_details.append(json.loads(line))

    # Save complete JSON
    json_path = state_output_dir / "all_churches_detail.json"
    save_to_json({
        "scrape_date": datetime.now(timezone.utc).isoformat(),
        "state": state_name,
        "churches_scraped": success_count,
        "churches_failed": fail_count,
        "church_details": all_details,
    }, json_path)

    # Generate viewable CSVs
    svc_count = generate_services_csv(all_details, state_output_dir / "all_services.csv")
    dated_count = generate_dated_services_csv(all_details, state_output_dir / "dated_services.csv")

    elapsed_total = (time.time() - start_time) / 60
    logger.info("\n" + "=" * 70)
    logger.info(f"DETAIL SCRAPE COMPLETE: {state_name.upper()}")
    logger.info(f"Churches: {success_count} scraped, {fail_count} failed (of {total})")
    logger.info(f"Total services: {svc_count}")
    logger.info(f"Dated service instances (12 weeks): {dated_count}")
    logger.info(f"Time: {elapsed_total:.1f} minutes")
    logger.info(f"Output: {state_output_dir}")
    logger.info("=" * 70)


# ============================================================================
# Phase 3: Dates only — regenerate dated_services.csv from existing JSONL
# ============================================================================

def run_dates_only(state_name: str):
    """
    Re-read existing church_details.jsonl and regenerate dated_services.csv.

    This is the fast path — no network requests, just date expansion from
    today forward. Takes seconds per state instead of hours.

    Args:
        state_name: Lowercase state name ("ohio", "texas")
    """
    state_output_dir = OUTPUT_DIR / state_name
    jsonl_path = state_output_dir / "church_details.jsonl"

    if not jsonl_path.exists():
        logger.warning(f"No JSONL data for {state_name} — skipping (run detail scrape first)")
        return

    # Read all details from JSONL
    all_details = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_details.append(json.loads(line))

    if not all_details:
        logger.warning(f"JSONL is empty for {state_name} — skipping")
        return

    # Regenerate dated CSV
    dated_count = generate_dated_services_csv(all_details, state_output_dir / "dated_services.csv")
    logger.info(f"{state_name.upper()}: {dated_count} dated service instances (12 weeks) from {len(all_details)} churches")


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Statewide Catholic church discovery and schedule scraping.",
        epilog="Example: python run_statewide.py ohio texas"
    )
    parser.add_argument(
        "states", nargs="+",
        help="States to scrape (e.g., ohio texas ca ny). Use full name or 2-letter code."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume an interrupted run (skip already-completed cities/churches)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only process the first N cities per state (for testing)"
    )
    parser.add_argument(
        "--discovery-only", action="store_true",
        help="Only run discovery (find churches), skip detail scraping"
    )
    parser.add_argument(
        "--detail-only", action="store_true",
        help="Only run detail scraping (requires prior discovery run)"
    )
    parser.add_argument(
        "--dates-only", action="store_true",
        help="Only regenerate dated_services.csv from existing JSONL (fast, no scraping)"
    )

    args = parser.parse_args()

    # Resolve state names — "all" expands to every known state
    state_configs = []
    if len(args.states) == 1 and args.states[0].lower() == "all":
        seen = set()
        for abbrev, name in STATE_ALIASES.values():
            if name not in seen:
                state_configs.append((abbrev, name))
                seen.add(name)
        state_configs.sort(key=lambda x: x[1])
    else:
        for s in args.states:
            abbrev, name = resolve_state(s)
            state_configs.append((abbrev, name))

    for state_abbrev, state_name in state_configs:
        logger.info(f"\n{'#' * 70}")
        logger.info(f"# STARTING: {state_name.upper()}")
        logger.info(f"{'#' * 70}\n")

        if args.dates_only:
            run_dates_only(state_name)
            continue

        if not args.detail_only:
            run_discovery(state_abbrev, state_name, resume=args.resume, limit=args.limit)

        if not args.discovery_only:
            run_detail_scrape(state_name, resume=args.resume)


if __name__ == "__main__":
    main()
