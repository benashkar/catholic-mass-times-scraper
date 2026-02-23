"""
csv_generator.py

Shared CSV output generation for church service data.

This module contains the date conversion helpers and CSV generation functions
used by both run_scrape_all.py (original 16-community flow) and
run_statewide.py (full state scraping).

HOW IT WORKS:
    1. Takes a list of church detail dicts (from scrape_church_detail())
    2. Flattens services into one-row-per-service format for all_services.csv
    3. Maps day-of-week + recurrence patterns to actual calendar dates for
       dated_services.csv (next 12 weeks)

USAGE:
    from src.etl.csv_generator import generate_services_csv, generate_dated_services_csv

    generate_services_csv(all_details, Path("data/output/all_services.csv"))
    generate_dated_services_csv(all_details, Path("data/output/dated_services.csv"))
"""

import csv
from pathlib import Path
from datetime import date, timedelta

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Map 3-letter day codes to Python weekday integers
# Python: Monday=0, Tuesday=1, ..., Sunday=6
DAY_CODE_TO_WEEKDAY = {
    "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6,
}

# Map day codes to full names for display
DAY_CODE_TO_FULL = {
    "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
    "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday",
}

# Recurrence pattern -> (which_occurrence, weekday) in the month
PATTERN_TO_OCCURRENCE = {
    "first_friday_(recurring)":       (1, 4),
    "first_saturday_(recurring)":     (1, 5),
    "first_sunday":                   (1, 6),
    "thursday_(before_first_friday)": None,  # Special case
}

# Sort order for service categories
CATEGORY_ORDER = {
    'Mass': 1, 'Confession': 2, 'Adoration': 3, 'Devotions': 4,
    'Education': 5, 'Community': 6, 'Other': 7,
}

# Sort order for days of week
DAY_ORDER = {
    'Mon': 1, 'Tue': 2, 'Wed': 3, 'Thu': 4, 'Fri': 5, 'Sat': 6, 'Sun': 7, '': 8,
}


# ============================================================================
# Date conversion helpers
# ============================================================================

def format_time_12h(time_str: str | None) -> str:
    """Convert "HH:MM:SS" (24-hour) to "H:MM AM/PM" format."""
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


def get_dates_for_day(day_code: str, start_date: date, num_weeks: int = 2) -> list[date]:
    """Get all dates matching a day-of-week code within the next N weeks."""
    target_weekday = DAY_CODE_TO_WEEKDAY.get(day_code)
    if target_weekday is None:
        return []
    days_ahead = (target_weekday - start_date.weekday()) % 7
    first = start_date + timedelta(days=days_ahead) if days_ahead > 0 else start_date
    return [first + timedelta(weeks=w) for w in range(num_weeks)]


def get_nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> date | None:
    """Find the Nth occurrence of a weekday in a given month."""
    first_day = date(year, month, 1)
    days_ahead = (weekday - first_day.weekday()) % 7
    target = first_day + timedelta(days=days_ahead) + timedelta(weeks=nth - 1)
    return target if target.month == month else None


def get_pattern_dates(pattern: str, start_date: date, num_months: int = 2) -> list[date]:
    """Get dates for a recurrence pattern within the next N months."""
    dates = []
    occurrence_info = PATTERN_TO_OCCURRENCE.get(pattern)

    if pattern == "thursday_(before_first_friday)":
        for month_offset in range(num_months):
            y = start_date.year
            m = start_date.month + month_offset
            if m > 12:
                y += (m - 1) // 12
                m = ((m - 1) % 12) + 1
            first_friday = get_nth_weekday_of_month(y, m, 4, 1)
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


# ============================================================================
# CSV generation functions
# ============================================================================

def generate_services_csv(all_details: list[dict], output_path: Path) -> int:
    """
    Generate a flat CSV with one row per service across all churches.

    Args:
        all_details: List of church detail dicts from scrape_church_detail()
        output_path: Path to write the CSV file

    Returns:
        Number of rows written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    csv_rows = []
    for detail in all_details:
        church = detail['church']

        for category, services in detail['services'].items():
            for svc in services:
                csv_rows.append({
                    'Church': church['name'],
                    'Address': f"{church.get('street', '')}, {church.get('city', '')}, {church.get('stateRegion', '')} {church.get('postalCode', '')}",
                    'Phone': church.get('phone', ''),
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

    csv_rows.sort(key=lambda r: (
        r['Church'],
        CATEGORY_ORDER.get(r['Category'], 99),
        DAY_ORDER.get(r['Day'], 99),
        r['Time Start'],
    ))

    if csv_rows:
        with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        logger.info(f"All services CSV: {output_path} ({len(csv_rows)} rows)")

    return len(csv_rows)


def generate_dated_services_csv(all_details: list[dict], output_path: Path) -> int:
    """
    Generate a CSV that maps day-of-week + patterns to actual calendar dates
    for the next 12 weeks.

    Args:
        all_details: List of church detail dicts from scrape_church_detail()
        output_path: Path to write the CSV file

    Returns:
        Number of rows written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    today = date.today()
    dated_rows = []

    for detail in all_details:
        church = detail['church']

        for category, services in detail['services'].items():
            for svc in services:
                day_code = svc.get('dayOfWeek')
                event_date_str = svc.get('eventDate')
                pattern = svc.get('pattern')

                actual_dates = []

                if event_date_str:
                    try:
                        d = date.fromisoformat(event_date_str)
                        if today <= d <= today + timedelta(days=84):
                            actual_dates.append(d)
                    except ValueError:
                        pass
                elif pattern:
                    actual_dates = get_pattern_dates(pattern, today, num_months=4)
                    actual_dates = [d for d in actual_dates if d <= today + timedelta(days=84)]
                elif day_code:
                    actual_dates = get_dates_for_day(day_code, today, num_weeks=12)

                for actual_date in actual_dates:
                    dated_rows.append({
                        'Date': actual_date.strftime('%a, %b %d, %Y'),
                        'Date Sort': actual_date.isoformat(),
                        'Day': DAY_CODE_TO_FULL.get(day_code, day_code or actual_date.strftime('%A')),
                        'Church': church['name'],
                        'Address': f"{church.get('street', '')}, {church.get('city', '')}",
                        'Phone': church.get('phone', ''),
                        'Category': category,
                        'Time': format_time_12h(svc.get('timeStart')),
                        'End Time': format_time_12h(svc.get('timeEnd')) if svc.get('timeEnd') else '',
                        'Service Name': svc.get('displayName', ''),
                        'Language': svc.get('language') or 'English',
                        'Location': svc.get('location') or '',
                        'Notes': svc.get('notes') or '',
                    })

    dated_rows.sort(key=lambda r: (
        r['Date Sort'],
        CATEGORY_ORDER.get(r['Category'], 99),
        r['Church'],
        r['Time'],
    ))

    if dated_rows:
        with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=dated_rows[0].keys())
            writer.writeheader()
            writer.writerows(dated_rows)
        logger.info(f"Dated services CSV: {output_path} ({len(dated_rows)} rows)")

    return len(dated_rows)
