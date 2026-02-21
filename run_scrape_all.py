"""
run_scrape_all.py

Phase 2: Full Scrape — Fetch complete schedules for ALL churches across
all 16 target Ohio communities.

HOW TO RUN:
    python run_scrape_all.py

    This will:
    1. Load the master church list (79 churches from Phase 1 discovery)
    2. Scrape full schedule detail for EVERY church from CatholicIndex.org
    3. Save raw JSON results to data/output/all_churches_detail.json
    4. Generate a viewable CSV with one row per service (mass, confession, etc.)
       at data/output/all_services.csv
    5. Generate a date-mapped CSV that converts day-of-week + patterns to
       actual dates for the next 2 weeks at data/output/dated_services.csv

EXPECTED RUNTIME:
    With 79 churches and ~1.5s rate limiting between requests,
    this takes approximately 2-3 minutes to complete.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

# Add project root to path so imports work when running this script directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import CHURCHES_DIR, OUTPUT_DIR
from src.scrapers.catholic_index import scrape_church_detail
from src.utils.file_io import load_from_csv, save_to_json
from src.utils.logger import get_logger

import csv

logger = get_logger(__name__)


# ============================================================================
# Date conversion helpers
# ============================================================================

# Map 3-letter day codes to Python weekday integers
# Python: Monday=0, Tuesday=1, ..., Sunday=6
DAY_CODE_TO_WEEKDAY = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}

# Map day codes to full names for display
DAY_CODE_TO_FULL = {
    "Mon": "Monday",
    "Tue": "Tuesday",
    "Wed": "Wednesday",
    "Thu": "Thursday",
    "Fri": "Friday",
    "Sat": "Saturday",
    "Sun": "Sunday",
}

# Recurrence pattern -> which occurrence in the month (1st, 2nd, 3rd, 4th, last)
# and which weekday. Used for "first_friday_(recurring)" etc.
PATTERN_TO_OCCURRENCE = {
    "first_friday_(recurring)":       (1, 4),   # 1st Friday (weekday 4)
    "first_saturday_(recurring)":     (1, 5),   # 1st Saturday (weekday 5)
    "first_sunday":                   (1, 6),   # 1st Sunday (weekday 6)
    "thursday_(before_first_friday)": None,      # Special case — computed relative to first friday
}


def get_dates_for_day(day_code: str, start_date: date, num_weeks: int = 2) -> list[date]:
    """
    Get all dates matching a day-of-week code within the next N weeks.

    Args:
        day_code: 3-letter day code ("Mon", "Tue", etc.)
        start_date: The first date to consider
        num_weeks: How many weeks to generate dates for

    Returns:
        List of date objects matching the day of week.

    Example:
        >>> get_dates_for_day("Sun", date(2026, 2, 20), num_weeks=2)
        [date(2026, 2, 22), date(2026, 3, 1)]
    """
    target_weekday = DAY_CODE_TO_WEEKDAY.get(day_code)
    if target_weekday is None:
        return []

    dates = []
    # Find the first occurrence of this weekday on or after start_date
    current = start_date
    days_ahead = (target_weekday - current.weekday()) % 7
    if days_ahead == 0 and current == start_date:
        # Today matches — include it
        first = current
    else:
        first = current + timedelta(days=days_ahead if days_ahead > 0 else 7)

    # Edge: if days_ahead is 0, include today
    if days_ahead == 0:
        first = current

    # Generate for num_weeks
    for week in range(num_weeks):
        d = first + timedelta(weeks=week)
        dates.append(d)

    return dates


def get_nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> date | None:
    """
    Find the Nth occurrence of a weekday in a given month.

    Args:
        year: The year
        month: The month (1-12)
        weekday: Python weekday (0=Mon, 6=Sun)
        nth: Which occurrence (1=first, 2=second, etc.)

    Returns:
        The date, or None if the Nth occurrence doesn't exist in this month.
    """
    # Start at the 1st of the month
    first_day = date(year, month, 1)
    # Find the first occurrence of the target weekday
    days_ahead = (weekday - first_day.weekday()) % 7
    first_occurrence = first_day + timedelta(days=days_ahead)
    # Jump ahead to the Nth occurrence
    target = first_occurrence + timedelta(weeks=nth - 1)
    # Verify it's still in the same month
    if target.month != month:
        return None
    return target


def get_pattern_dates(pattern: str, start_date: date, num_months: int = 2) -> list[date]:
    """
    Get dates for a recurrence pattern (e.g., "first_friday_(recurring)")
    within the next N months.

    Args:
        pattern: The CatholicIndex pattern string
        start_date: First date to consider
        num_months: How many months to look ahead

    Returns:
        List of matching dates.
    """
    dates = []
    occurrence_info = PATTERN_TO_OCCURRENCE.get(pattern)

    if pattern == "thursday_(before_first_friday)":
        # Special case: Thursday before First Friday
        for month_offset in range(num_months):
            y = start_date.year
            m = start_date.month + month_offset
            if m > 12:
                y += (m - 1) // 12
                m = ((m - 1) % 12) + 1
            first_friday = get_nth_weekday_of_month(y, m, 4, 1)  # 4=Friday
            if first_friday:
                thursday = first_friday - timedelta(days=1)
                if thursday >= start_date:
                    dates.append(thursday)
    elif occurrence_info:
        nth, weekday = occurrence_info
        for month_offset in range(num_months):
            y = start_date.year
            m = start_date.month + month_offset
            if m > 12:
                y += (m - 1) // 12
                m = ((m - 1) % 12) + 1
            target = get_nth_weekday_of_month(y, m, weekday, nth)
            if target and target >= start_date:
                dates.append(target)

    return dates


def format_time_12h(time_str: str | None) -> str:
    """
    Convert "HH:MM:SS" (24-hour) to "H:MM AM/PM" format.

    Args:
        time_str: Time string like "08:30:00" or "19:00:00"

    Returns:
        Formatted string like "8:30 AM" or "7:00 PM".
        Returns "TBD" if input is None.
    """
    if not time_str:
        return "TBD"
    try:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = parts[1]
        ampm = "AM" if hour < 12 else "PM"
        if hour == 0:
            hour = 12
        elif hour > 12:
            hour -= 12
        return f"{hour}:{minute} {ampm}"
    except (ValueError, IndexError):
        return time_str


# ============================================================================
# Main scrape logic
# ============================================================================

def run_full_scrape():
    """
    Scrape full detail for all churches in the master list, then generate
    viewable CSV output files.
    """
    # Load the master church list from Phase 1 discovery
    master_path = CHURCHES_DIR / "master_church_list.csv"
    master_list = load_from_csv(master_path)

    if not master_list:
        logger.error(f"Could not load master church list from {master_path}")
        logger.error("Run run_discovery.py first to build the church list.")
        return

    logger.info("=" * 70)
    logger.info("PHASE 2: Full Schedule Scrape — Starting")
    logger.info(f"Churches to scrape: {len(master_list)}")
    logger.info("=" * 70)

    # Scrape each church's full detail
    all_details = []
    success_count = 0
    fail_count = 0

    for i, church in enumerate(master_list, 1):
        slug = church['slug']
        name = church['name']
        city = church.get('city', '?')
        communities = church.get('serving_communities', '')

        logger.info(f"[{i}/{len(master_list)}] {name} ({city}) — serves: {communities}")

        detail = scrape_church_detail(slug)
        if detail:
            # Add serving_communities to the detail for the output
            detail['serving_communities'] = communities
            all_details.append(detail)
            success_count += 1
        else:
            fail_count += 1
            logger.warning(f"  FAILED to scrape {name}")

    # Save raw JSON (all church details)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "all_churches_detail.json"
    save_to_json({
        "scrape_date": datetime.now(timezone.utc).isoformat(),
        "churches_scraped": success_count,
        "churches_failed": fail_count,
        "church_details": all_details,
    }, json_path)
    logger.info(f"\nRaw JSON saved to: {json_path}")

    # ========================================================================
    # Generate viewable CSV: One row per service
    # ========================================================================
    logger.info("\nGenerating viewable CSV output...")

    csv_rows = []
    for detail in all_details:
        church = detail['church']
        communities = detail.get('serving_communities', '')

        for category, services in detail['services'].items():
            for svc in services:
                csv_rows.append({
                    'Church': church['name'],
                    'Address': f"{church.get('street', '')}, {church.get('city', '')}, {church.get('stateRegion', '')} {church.get('postalCode', '')}",
                    'Phone': church.get('phone', ''),
                    'Communities Served': communities,
                    'Category': category,
                    'Day': svc.get('dayOfWeek') or '',
                    'Time Start': format_time_12h(svc.get('timeStart')),
                    'Time End': format_time_12h(svc.get('timeEnd')) if svc.get('timeEnd') else '',
                    'Service Name': svc.get('displayName', ''),
                    'Schedule Type': svc.get('scheduleType', ''),
                    'Language': svc.get('language') or 'English',
                    'Location': svc.get('location') or '',
                    'Pattern': svc.get('pattern') or '',
                    'Event Date': svc.get('eventDate') or '',
                    'Time Relation': svc.get('timeRelation') or '',
                    'Reference Service': svc.get('referenceService') or '',
                    'Notes': svc.get('notes') or '',
                })

    # Sort: by Church name, then by category priority, then by day, then by time
    category_order = {'Mass': 1, 'Confession': 2, 'Adoration': 3, 'Devotions': 4,
                      'Education': 5, 'Community': 6, 'Other': 7}
    day_order = {'Mon': 1, 'Tue': 2, 'Wed': 3, 'Thu': 4, 'Fri': 5, 'Sat': 6, 'Sun': 7, '': 8}

    csv_rows.sort(key=lambda r: (
        r['Church'],
        category_order.get(r['Category'], 99),
        day_order.get(r['Day'], 99),
        r['Time Start'],
    ))

    # Write the all-services CSV
    services_csv_path = OUTPUT_DIR / "all_services.csv"
    if csv_rows:
        with open(services_csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        logger.info(f"All services CSV saved to: {services_csv_path} ({len(csv_rows)} rows)")

    # ========================================================================
    # Generate dated CSV: Convert day-of-week + patterns to actual dates
    # ========================================================================
    logger.info("\nGenerating dated services CSV (next 2 weeks)...")

    today = date.today()
    dated_rows = []

    for detail in all_details:
        church = detail['church']
        communities = detail.get('serving_communities', '')

        for category, services in detail['services'].items():
            for svc in services:
                day_code = svc.get('dayOfWeek')
                event_date_str = svc.get('eventDate')
                pattern = svc.get('pattern')
                schedule_type = svc.get('scheduleType', '')

                # Determine the actual dates this service occurs on
                actual_dates = []

                if event_date_str:
                    # One-time event with a specific date
                    try:
                        d = date.fromisoformat(event_date_str)
                        if d >= today and d <= today + timedelta(days=14):
                            actual_dates.append(d)
                    except ValueError:
                        pass

                elif pattern:
                    # Monthly recurrence pattern (First Friday, etc.)
                    actual_dates = get_pattern_dates(pattern, today, num_months=2)
                    # Filter to next 2 weeks only
                    actual_dates = [d for d in actual_dates if d <= today + timedelta(days=14)]

                elif day_code:
                    # Regular weekly recurrence
                    actual_dates = get_dates_for_day(day_code, today, num_weeks=2)

                # Create a row for each actual date
                for actual_date in actual_dates:
                    dated_rows.append({
                        'Date': actual_date.strftime('%a, %b %d, %Y'),
                        'Date Sort': actual_date.isoformat(),
                        'Day': DAY_CODE_TO_FULL.get(day_code, day_code or actual_date.strftime('%A')),
                        'Church': church['name'],
                        'Address': f"{church.get('street', '')}, {church.get('city', '')}",
                        'Phone': church.get('phone', ''),
                        'Communities Served': communities,
                        'Category': category,
                        'Time': format_time_12h(svc.get('timeStart')),
                        'End Time': format_time_12h(svc.get('timeEnd')) if svc.get('timeEnd') else '',
                        'Service Name': svc.get('displayName', ''),
                        'Language': svc.get('language') or 'English',
                        'Location': svc.get('location') or '',
                        'Notes': svc.get('notes') or '',
                    })

    # Sort dated rows by date, then church, then time
    dated_rows.sort(key=lambda r: (
        r['Date Sort'],
        category_order.get(r['Category'], 99),
        r['Church'],
        r['Time'],
    ))

    # Write the dated services CSV
    dated_csv_path = OUTPUT_DIR / "dated_services.csv"
    if dated_rows:
        with open(dated_csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=dated_rows[0].keys())
            writer.writeheader()
            writer.writerows(dated_rows)
        logger.info(f"Dated services CSV saved to: {dated_csv_path} ({len(dated_rows)} rows)")

    # ========================================================================
    # Print Summary
    # ========================================================================
    logger.info("\n" + "=" * 70)
    logger.info("FULL SCRAPE COMPLETE")
    logger.info(f"Churches scraped: {success_count}/{len(master_list)} ({fail_count} failed)")
    logger.info(f"Total services found: {len(csv_rows)}")
    logger.info(f"Dated service instances (next 2 weeks): {len(dated_rows)}")
    logger.info(f"\nOutput files:")
    logger.info(f"  Raw JSON:          {json_path}")
    logger.info(f"  All Services CSV:  {services_csv_path}")
    logger.info(f"  Dated Services CSV: {dated_csv_path}")
    logger.info("=" * 70)

    return all_details


if __name__ == "__main__":
    run_full_scrape()
