"""
run_scrape_sample.py

Scrapes full schedules for a sample of churches near one community.
Used to review data quality before running a full scrape of all 79 churches.

HOW TO RUN:
    python run_scrape_sample.py

    This will:
    1. Load the Grove City community church list
    2. Scrape full detail for the closest 10 churches
    3. Save results to data/output/grove_city_sample.json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import CHURCHES_DIR, OUTPUT_DIR
from src.scrapers.catholic_index import scrape_church_detail
from src.utils.file_io import load_from_json, save_to_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


def run_sample_scrape(community_file: str = "grove_city.json", max_churches: int = 10):
    """
    Scrape full detail for a sample of churches from one community.

    Args:
        community_file: Name of the JSON file in data/churches/
        max_churches: Maximum number of churches to scrape (closest first)
    """
    # Load the community's church list
    data = load_from_json(CHURCHES_DIR / community_file)
    if not data:
        logger.error(f"Could not load {community_file}")
        return

    community_name = data['community']
    churches = data['churches']

    # Sort by distance (closest first) and take the first N
    # distanceMiles is a string like "0.0" so convert to float for sorting
    churches_sorted = sorted(churches, key=lambda c: float(c.get('distanceMiles', '999')))
    sample = churches_sorted[:max_churches]

    logger.info(f"Scraping detail for {len(sample)} closest churches to {community_name}")

    # Scrape each church's full detail
    results = []
    for i, church in enumerate(sample, 1):
        slug = church['slug']
        name = church['name']
        distance = church.get('distanceMiles', '?')

        logger.info(f"[{i}/{len(sample)}] {name} ({distance} mi)")

        detail = scrape_church_detail(slug)
        if detail:
            results.append(detail)
        else:
            logger.warning(f"Failed to get detail for {name}")

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{community_name.lower().replace(' ', '_')}_sample.json"
    save_to_json({
        "community": community_name,
        "churches_scraped": len(results),
        "churches_failed": len(sample) - len(results),
        "church_details": results,
    }, output_path)

    # Print a summary of what we found
    logger.info("\n" + "=" * 70)
    logger.info(f"SAMPLE SCRAPE COMPLETE — {community_name}")
    logger.info(f"Churches scraped: {len(results)}/{len(sample)}")
    logger.info("=" * 70)

    for detail in results:
        church = detail['church']
        services = detail['services']
        masses = services.get('Mass', [])
        confessions = services.get('Confession', [])
        adorations = services.get('Adoration', [])
        devotions = services.get('Devotions', [])

        logger.info(f"\n  {church['name']}")
        logger.info(f"  {church['street']}, {church['city']}, {church['stateRegion']} {church.get('postalCode', '')}")
        logger.info(f"  Phone: {church.get('phone', 'N/A')} | Updated: {church.get('updatedAt', 'N/A')}")
        logger.info(f"  Masses: {len(masses)} | Confessions: {len(confessions)} | Adoration: {len(adorations)} | Devotions: {len(devotions)}")

        if masses:
            logger.info("  --- Mass Times ---")
            for m in masses:
                day = m.get('dayOfWeek') or m.get('eventDate') or '?'
                time = m.get('timeStart') or '?'
                name_str = m.get('displayName', 'Mass')
                stype = m.get('scheduleType', '')
                lang = m.get('language', '')
                notes = m.get('notes', '')
                extra = []
                if lang:
                    extra.append(lang)
                if notes:
                    extra.append(notes)
                extra_str = f" ({'; '.join(extra)})" if extra else ""
                logger.info(f"    {day:>3} {time} — {name_str} [{stype}]{extra_str}")

        if confessions:
            logger.info("  --- Confession Times ---")
            for c in confessions:
                day = c.get('dayOfWeek') or '?'
                start = c.get('timeStart') or '?'
                end = c.get('timeEnd') or ''
                notes = c.get('notes') or ''
                time_str = f"{start}–{end}" if end else start
                logger.info(f"    {day:>3} {time_str} {notes}")

    return results


if __name__ == "__main__":
    run_sample_scrape()
