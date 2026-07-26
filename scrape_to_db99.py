"""
scrape_to_db99.py — Scrape mass times from CatholicIndex.org directly to db99.

Stateless scraper designed for Render cron jobs (no local files needed).
Reads the church list from db99, scrapes each church's detail page,
and UPSERTs directly back to db99.

Usage:
    python scrape_to_db99.py                    # All states
    python scrape_to_db99.py --state OH         # One state
    python scrape_to_db99.py --limit 50         # First 50 churches per state
    python scrape_to_db99.py --batch-size 10    # 10 churches between commits
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql

from src.scrapers.catholic_index import scrape_church_detail

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


def get_connection():
    """Connect to db99."""
    try:
        import boto3

        client = boto3.client("secretsmanager", region_name="us-east-1")
        resp = client.get_secret_value(SecretId="/ben/ai-tool/db99")
        secret = json.loads(resp["SecretString"])
        host = os.getenv("DB_HOST") or secret.get("DB_HOST") or "10.10.0.8"
        user = secret["DB_USER"]
        password = secret["DB_PASSWORD"]
    except Exception:
        host = os.getenv("DB_HOST", "10.10.0.8")
        user = os.getenv("DB_USER", "")
        password = os.getenv("DB_PASSWORD", "")

    return pymysql.connect(
        host=host,
        port=3306,
        user=user,
        password=password,
        database="church_scrapes",
        connect_timeout=30,
        read_timeout=300,
        write_timeout=300,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ---------------------------------------------------------------------------
# Category/schedule/language mappings (same as sync_to_db99.py)
# ---------------------------------------------------------------------------

CATEGORY_MAP = {
    "Mass": "mass",
    "Confession": "confession",
    "Adoration": "adoration",
    "Devotions": "devotions",
    "Education": "education",
    "Community": "community",
    "Other": "other",
}

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

PATTERN_MAP = {
    "first_friday_(recurring)": "first_friday",
    "first_saturday_(recurring)": "first_saturday",
    "first_sunday": "first_sunday",
    "thursday_(before_first_friday)": "thursday_before_first_friday",
    "third_sunday": "third_sunday",
    "last_wednesday": "last_wednesday",
}

CATEGORY_DISPLAY = {
    "mass": "Mass",
    "confession": "Confession",
    "adoration": "Adoration",
    "devotions": "Devotions",
    "education": "Education",
    "community": "Community",
    "other": "Other",
}


def sanitize_display_name(name, category_code):
    """Clean up display_name values."""
    import re

    if not name or not name.strip():
        return CATEGORY_DISPLAY.get(category_code, "Other")
    name = re.sub(r"[^\x20-\x7E\u00A0-\uFFFF]", "", name).strip()
    if not name or name.lower() == "type":
        return CATEGORY_DISPLAY.get(category_code, "Other")
    return name


# ---------------------------------------------------------------------------
# Upsert one church + services directly to db99
# ---------------------------------------------------------------------------


def upsert_church(cur, detail, state_code):
    """UPSERT one church and its services directly from scraped detail dict."""
    church_raw = detail.get("church", {})
    slug = church_raw.get("slug")
    if not slug:
        return False

    name = church_raw.get("name") or ""
    street = church_raw.get("street")
    city = church_raw.get("city") or ""
    postal_code = church_raw.get("postalCode")
    lat = church_raw.get("latitude")
    lng = church_raw.get("longitude")
    phone = church_raw.get("phone")
    website_url = church_raw.get("website_resolved") or church_raw.get("website")

    livestream_url = church_raw.get("livestreamUrl")
    if livestream_url and livestream_url.startswith("#"):
        livestream_url = None
    has_livestream = 1 if (livestream_url and not livestream_url.startswith("/api/")) else 0
    has_perp_adoration = 1 if church_raw.get("hasPerpetualAdoration") else 0
    stream_count = church_raw.get("streamCount", 0) or 0

    schedule_updated_at = None
    if church_raw.get("updatedAt"):
        try:
            schedule_updated_at = datetime.fromisoformat(
                church_raw["updatedAt"].replace("Z", "+00:00")
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            pass

    last_scraped_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    source_url = detail.get("source_url")

    services_dict = detail.get("services", {})
    mass_count = len(services_dict.get("Mass", []))
    confession_count = len(services_dict.get("Confession", []))
    adoration_count = len(services_dict.get("Adoration", []))

    # UPSERT church
    cur.execute(
        """
        INSERT INTO church (
            slug, name, street, city, state_code, postal_code,
            latitude, longitude, phone, website_url, livestream_url,
            has_perpetual_adoration, has_livestream,
            mass_count, confession_count, adoration_count, stream_count,
            schedule_updated_at, source_url, last_scraped_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            name=VALUES(name), street=VALUES(street), city=VALUES(city),
            postal_code=VALUES(postal_code), latitude=VALUES(latitude),
            longitude=VALUES(longitude), phone=VALUES(phone),
            website_url=VALUES(website_url), livestream_url=VALUES(livestream_url),
            has_perpetual_adoration=VALUES(has_perpetual_adoration),
            has_livestream=VALUES(has_livestream),
            mass_count=VALUES(mass_count), confession_count=VALUES(confession_count),
            adoration_count=VALUES(adoration_count), stream_count=VALUES(stream_count),
            schedule_updated_at=VALUES(schedule_updated_at),
            source_url=VALUES(source_url), last_scraped_at=VALUES(last_scraped_at)
    """,
        (
            slug,
            name,
            street,
            city,
            state_code,
            postal_code,
            lat,
            lng,
            phone,
            website_url,
            livestream_url,
            has_perp_adoration,
            has_livestream,
            mass_count,
            confession_count,
            adoration_count,
            stream_count,
            schedule_updated_at,
            source_url,
            last_scraped_at,
        ),
    )

    # Get church_id
    cur.execute("SELECT church_id FROM church WHERE slug = %s", (slug,))
    row = cur.fetchone()
    if not row:
        return False
    church_id = row["church_id"]

    # UPSERT services
    for category_name, service_list in services_dict.items():
        for svc in service_list:
            service_id_src = svc.get("serviceId", "")
            if not service_id_src:
                continue

            category_code = CATEGORY_MAP.get(category_name, "other")
            schedule_type = SCHEDULE_TYPE_MAP.get(svc.get("scheduleType", ""), "other")
            day_code = svc.get("dayOfWeek")
            time_start = svc.get("timeStart")
            time_end = svc.get("timeEnd")
            event_date = svc.get("eventDate")
            notes_raw = svc.get("notes")

            raw_name = svc.get("displayName", category_name)
            display_name = sanitize_display_name(raw_name, category_code)

            lang_raw = svc.get("language")
            language_code = LANGUAGE_MAP.get(lang_raw, None) if lang_raw else None

            pattern_raw = svc.get("pattern")
            pattern_code = PATTERN_MAP.get(pattern_raw, pattern_raw) if pattern_raw else None

            relation_code = svc.get("timeRelation")
            reference_service = svc.get("referenceService")
            offset_minutes = svc.get("offsetMinutes")
            location = svc.get("location")

            cur.execute(
                """
                INSERT INTO service (
                    church_id, source_service_id, category_code,
                    schedule_type_code, day_code, time_start, time_end,
                    event_date, language_code, pattern_code,
                    relation_code, reference_service, offset_minutes,
                    location, display_name, notes_raw
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    category_code=VALUES(category_code),
                    schedule_type_code=VALUES(schedule_type_code),
                    day_code=VALUES(day_code),
                    time_start=VALUES(time_start), time_end=VALUES(time_end),
                    event_date=VALUES(event_date), language_code=VALUES(language_code),
                    pattern_code=VALUES(pattern_code), relation_code=VALUES(relation_code),
                    reference_service=VALUES(reference_service),
                    offset_minutes=VALUES(offset_minutes),
                    location=VALUES(location), display_name=VALUES(display_name),
                    notes_raw=VALUES(notes_raw)
            """,
                (
                    church_id,
                    service_id_src,
                    category_code,
                    schedule_type,
                    day_code,
                    time_start,
                    time_end,
                    event_date,
                    language_code,
                    pattern_code,
                    relation_code,
                    reference_service,
                    offset_minutes,
                    location,
                    display_name,
                    notes_raw,
                ),
            )

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Scrape mass times directly to db99")
    parser.add_argument("--state", type=str, help="One state code (e.g. OH)")
    parser.add_argument("--limit", type=int, default=0, help="Max churches per state (0=all)")
    parser.add_argument(
        "--batch-size", type=int, default=10, help="Churches between progress prints"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Scrape Mass Times -> db99 (Direct)")
    print("=" * 60)

    conn = get_connection()
    cur = conn.cursor()

    # Get churches to scrape from db99 (not from local files)
    if args.state:
        cur.execute(
            "SELECT slug, state_code, name FROM church WHERE state_code = %s ORDER BY slug",
            (args.state.upper(),),
        )
    else:
        cur.execute("SELECT slug, state_code, name FROM church ORDER BY state_code, slug")

    churches = cur.fetchall()
    if args.limit:
        churches = churches[: args.limit]

    print(f"Churches to scrape: {len(churches)}")

    start = time.time()
    success = 0
    failed = 0
    skipped = 0

    for i, church in enumerate(churches):
        slug = church["slug"]
        state_code = church["state_code"]

        try:
            detail = scrape_church_detail(slug)
            if detail:
                upsert_church(cur, detail, state_code)
                success += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  [ERR] {slug}: {e}")

        if (i + 1) % args.batch_size == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(churches) - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i + 1}/{len(churches)}] {state_code} "
                f"ok={success} skip={skipped} fail={failed} "
                f"({rate:.1f}/s, ~{remaining / 60:.0f}min left)"
            )

        # Small delay to avoid rate limiting
        time.sleep(0.3)

    elapsed = time.time() - start

    # Log the run
    cur.execute(
        """
        INSERT INTO scrape_log (
            scrape_type, completed_at, status,
            communities_scraped, churches_scraped, services_upserted,
            errors, notes
        ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
    """,
        (
            "daily_mass_times",
            "completed" if failed == 0 else "partial",
            len(set(c["state_code"] for c in churches)),
            success,
            None,
            str(failed) if failed else None,
            f"ok={success} skip={skipped} fail={failed} in {elapsed:.0f}s",
        ),
    )

    print(f"\n{'=' * 60}")
    print("  SCRAPE SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Churches: {success} ok, {skipped} skipped, {failed} failed")
    print(f"  Time: {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    print("[OK] Done!")

    conn.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
