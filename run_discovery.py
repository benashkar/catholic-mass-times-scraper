"""
run_discovery.py

Phase 1: Church Discovery Script

Discovers all Catholic churches serving each of the 16 target Ohio communities
by scraping CatholicIndex.org city pages. Outputs a master church list CSV
and per-community JSON files.

HOW TO RUN:
    python run_discovery.py

    This will:
    1. Fetch the CatholicIndex mass-times page for each community
    2. Extract all churches within the default 25-mile radius
    3. Deduplicate churches (the same church may appear near multiple communities)
    4. Save per-community results to data/churches/{community}.json
    5. Save a master church list to data/churches/master_church_list.csv

OUTPUT FILES:
    data/churches/master_church_list.csv — Deduplicated master list of all churches
    data/churches/{community_slug}.json  — Per-community church data with distances

EXPECTED RUNTIME:
    With 16 communities and ~1.5s rate limiting between requests,
    this takes approximately 30-45 seconds to complete.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path so imports work when running this script directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import CHURCHES_DIR, TARGET_COMMUNITIES
from src.scrapers.catholic_index import discover_churches
from src.utils.file_io import save_to_csv, save_to_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


def run_discovery():
    """
    Run church discovery for all target communities.

    This is the main function that:
    1. Iterates through each target community
    2. Calls the CatholicIndex scraper to find nearby churches
    3. Saves per-community results as JSON
    4. Builds a deduplicated master list and saves as CSV

    The master list is deduplicated by church slug — if the same church
    appears near multiple communities (e.g., a church in Columbus might
    be within 25 miles of both Grove City and Hilliard), it only appears
    once in the master list, but we track which communities it serves.
    """
    logger.info("=" * 60)
    logger.info("PHASE 1: Church Discovery — Starting")
    logger.info(f"Target communities: {len(TARGET_COMMUNITIES)}")
    logger.info("=" * 60)

    # Track all churches across all communities (keyed by slug for dedup)
    # slug -> church dict (with added 'serving_communities' list)
    all_churches = {}

    # Track stats
    communities_success = 0
    communities_failed = 0

    # Cache for fallback city results so we don't re-fetch Columbus 12 times
    # key = "CityName-STATE" -> value = list of church dicts
    fallback_cache = {}

    for i, community in enumerate(TARGET_COMMUNITIES, 1):
        name = community["name"]
        state = community["state"]
        fallback = community.get("fallback_city")

        logger.info(f"\n--- [{i}/{len(TARGET_COMMUNITIES)}] {name}, {state} ---")

        if fallback:
            # This community doesn't have its own CatholicIndex page.
            # Use a nearby larger city's page instead.
            cache_key = f"{fallback}-{state}"
            logger.info(f"Using fallback city: {fallback} (no CatholicIndex page for {name})")

            if cache_key not in fallback_cache:
                fallback_cache[cache_key] = discover_churches(fallback, state)

            churches = fallback_cache[cache_key]

            # Tag each church with which community search found it
            for church in churches:
                church = {**church}  # Don't mutate the cached version
            # We keep all churches from the fallback (25-mile radius from Columbus
            # center covers all these small communities). The serving_communities
            # dedup logic below will properly attribute them.
        else:
            # This community has its own CatholicIndex page
            churches = discover_churches(name, state)

        if churches:
            communities_success += 1

            # Save per-community results as JSON
            community_slug = name.lower().replace(" ", "_")
            json_path = CHURCHES_DIR / f"{community_slug}.json"
            save_to_json(
                {
                    "community": name,
                    "state": state,
                    "county": community["county"],
                    "zip_codes": community["zips"],
                    "churches_found": len(churches),
                    "scraped_at": datetime.now(UTC).isoformat(),
                    "churches": churches,
                },
                json_path,
            )

            # Add churches to master list, deduplicating by slug
            for church in churches:
                slug = church["slug"]
                if slug not in all_churches:
                    # First time seeing this church — add it
                    all_churches[slug] = {
                        "slug": slug,
                        "name": church["name"],
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
                        "serving_communities": [name],
                        "source_url": church.get("source_url", ""),
                        "last_scraped": church.get("last_scraped", ""),
                    }
                else:
                    # Already seen this church — just add this community to its list
                    if name not in all_churches[slug]["serving_communities"]:
                        all_churches[slug]["serving_communities"].append(name)
        else:
            communities_failed += 1
            logger.warning(f"No churches found for {name}, {state}")

    # Convert master list to a flat list for CSV
    # Join the serving_communities list into a semicolon-separated string
    # (semicolons because commas would conflict with CSV)
    master_list = []
    for church in sorted(all_churches.values(), key=lambda c: (c["city"], c["name"])):
        church_row = {**church}
        church_row["serving_communities"] = "; ".join(church["serving_communities"])
        master_list.append(church_row)

    # Save master list as CSV
    if master_list:
        csv_path = CHURCHES_DIR / "master_church_list.csv"
        save_to_csv(master_list, csv_path)

    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 1: Church Discovery — Complete")
    logger.info(
        f"Communities scraped successfully: {communities_success}/{len(TARGET_COMMUNITIES)}"
    )
    logger.info(f"Communities failed: {communities_failed}")
    logger.info(f"Total unique churches found: {len(all_churches)}")
    logger.info(f"Master list saved to: {CHURCHES_DIR / 'master_church_list.csv'}")
    logger.info("=" * 60)

    return master_list


if __name__ == "__main__":
    run_discovery()
