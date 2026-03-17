"""
sync_to_db99.py -- Sync scraped church data from local files to MySQL (db99 RDS).

Reads church_details.jsonl, all_services.csv, and bulletin_names.csv from each
state directory and UPSERTs into the church_scrapes database.  Includes data
sanitization for display_name quality issues.

Usage:
    python sync_to_db99.py                  # Sync all states
    python sync_to_db99.py --state ohio     # Sync one state
    python sync_to_db99.py --dry-run        # Validate without inserting
    python sync_to_db99.py --skip-bulletins # Skip bulletin data (faster)
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
from src.utils.db_connection import get_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "output")
BATCH_SIZE = 1000

DIR_TO_STATE_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new_hampshire": "NH", "new_jersey": "NJ",
    "new_mexico": "NM", "new_york": "NY", "north_carolina": "NC",
    "north_dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode_island": "RI", "south_carolina": "SC",
    "south_dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west_virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

STATE_CODE_TO_NAME = {v: k.replace("_", " ").title() for k, v in DIR_TO_STATE_CODE.items()}

CATEGORY_MAP = {
    "Mass": "mass", "Confession": "confession", "Adoration": "adoration",
    "Devotions": "devotions", "Education": "education", "Community": "community",
    "Other": "other",
}

SCHEDULE_TYPE_MAP = {
    "sunday": "sunday", "saturday": "saturday", "weekday": "weekday",
    "specific_weekday": "specific_weekday", "monthly": "monthly",
    "special_event": "special_event", "parish_event": "parish_event",
    "other": "other",
}

LANGUAGE_MAP = {
    "english": "en", "spanish": "es", "latin": "la", "bilingual": "bi",
    "vietnamese": "vi", "korean": "ko", "polish": "pl", "portuguese": "pt",
}

PATTERN_MAP = {
    "first_friday_(recurring)": "first_friday",
    "first_saturday_(recurring)": "first_saturday",
    "first_sunday": "first_sunday",
    "thursday_(before_first_friday)": "thursday_before_first_friday",
    "third_sunday": "third_sunday",
    "last_wednesday": "last_wednesday",
}

# Category code -> proper display name (for sanitization fallback)
CATEGORY_DISPLAY = {
    "mass": "Mass", "confession": "Confession", "adoration": "Adoration",
    "devotions": "Devotions", "education": "Education",
    "community": "Community", "other": "Other",
}

# ---------------------------------------------------------------------------
# Display name sanitization
# ---------------------------------------------------------------------------
_NON_PRINTABLE_RE = re.compile(r"[^\x20-\x7E\u00A0-\uFFFF]")
_LONE_DAY_RE = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$", re.IGNORECASE
)


def sanitize_display_name(name, category_code):
    """Clean up messy display_name values before DB insert.

    Rules:
      - Empty/whitespace-only -> category display name (e.g. "Mass")
      - "Type" (leaked header) -> category display name
      - Non-printable / corrupted chars -> stripped
      - Lone day name ("Friday") -> "Friday Mass" (append category)
    """
    if not name or not name.strip():
        return CATEGORY_DISPLAY.get(category_code, "Other")

    name = _NON_PRINTABLE_RE.sub("", name).strip()

    if not name:
        return CATEGORY_DISPLAY.get(category_code, "Other")

    if name.lower() == "type":
        return CATEGORY_DISPLAY.get(category_code, "Other")

    match = _LONE_DAY_RE.match(name)
    if match:
        cat_label = CATEGORY_DISPLAY.get(category_code, "")
        if cat_label and cat_label.lower() != "other":
            return f"{match.group(1)} {cat_label}"

    return name


# ---------------------------------------------------------------------------
# Church loading
# ---------------------------------------------------------------------------
def load_churches(cur, state_dir, state_code, dry_run=False):
    """Load churches from church_details.jsonl. Returns slug->church_id map."""
    jsonl_path = os.path.join(state_dir, "church_details.jsonl")
    if not os.path.exists(jsonl_path):
        print(f"  [--] No church_details.jsonl for {state_code}")
        return {}

    church_rows = []
    slugs = []
    parse_errors = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            church_raw = record.get("church", {})
            slug = church_raw.get("slug")
            if not slug:
                continue

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

            last_scraped_at = None
            if record.get("last_scraped"):
                try:
                    last_scraped_at = datetime.fromisoformat(
                        record["last_scraped"]
                    ).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, AttributeError):
                    pass

            source_url = record.get("source_url")
            community_insights = record.get("communityInsights")
            if community_insights == "INSUFFICIENT_DATA":
                community_insights = None

            services_dict = record.get("services", {})
            mass_count = len(services_dict.get("Mass", []))
            confession_count = len(services_dict.get("Confession", []))
            adoration_count = len(services_dict.get("Adoration", []))

            church_rows.append((
                slug, name, street, city, state_code, postal_code,
                lat, lng, phone, website_url, livestream_url,
                has_perp_adoration, has_livestream,
                mass_count, confession_count, adoration_count, stream_count,
                schedule_updated_at, source_url, community_insights,
                last_scraped_at
            ))
            slugs.append(slug)

    if parse_errors:
        print(f"  [--] {parse_errors} JSON parse errors skipped")

    if not church_rows:
        return {}

    if dry_run:
        print(f"  [OK] Churches validated: {len(slugs)}")
        return {s: i for i, s in enumerate(slugs, 1)}

    CHURCH_SQL = """
        INSERT INTO church (
            slug, name, street, city, state_code, postal_code,
            latitude, longitude, phone, website_url, livestream_url,
            has_perpetual_adoration, has_livestream,
            mass_count, confession_count, adoration_count, stream_count,
            schedule_updated_at, source_url, community_insights,
            last_scraped_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s
        )
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            street = VALUES(street),
            city = VALUES(city),
            postal_code = VALUES(postal_code),
            latitude = VALUES(latitude),
            longitude = VALUES(longitude),
            phone = VALUES(phone),
            website_url = VALUES(website_url),
            livestream_url = VALUES(livestream_url),
            has_perpetual_adoration = VALUES(has_perpetual_adoration),
            has_livestream = VALUES(has_livestream),
            mass_count = VALUES(mass_count),
            confession_count = VALUES(confession_count),
            adoration_count = VALUES(adoration_count),
            stream_count = VALUES(stream_count),
            schedule_updated_at = VALUES(schedule_updated_at),
            source_url = VALUES(source_url),
            community_insights = VALUES(community_insights),
            last_scraped_at = VALUES(last_scraped_at)
    """

    for i in range(0, len(church_rows), BATCH_SIZE):
        cur.executemany(CHURCH_SQL, church_rows[i:i + BATCH_SIZE])

    cur.execute(
        "SELECT slug, church_id FROM church WHERE state_code = %s",
        (state_code,)
    )
    slug_to_id = {row["slug"]: row["church_id"] for row in cur.fetchall()}

    loaded = sum(1 for s in slugs if s in slug_to_id)
    print(f"  [OK] Churches upserted: {loaded}")
    return slug_to_id


# ---------------------------------------------------------------------------
# Service loading (with sanitization)
# ---------------------------------------------------------------------------
def load_services(cur, state_dir, state_code, slug_to_id, dry_run=False):
    """Load services from church_details.jsonl with display_name sanitization."""
    jsonl_path = os.path.join(state_dir, "church_details.jsonl")
    if not os.path.exists(jsonl_path):
        return

    service_rows = []
    sanitized_count = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            church_slug = record.get("church", {}).get("slug")
            if not church_slug or church_slug not in slug_to_id:
                continue

            church_id = slug_to_id[church_slug]
            services_dict = record.get("services", {})

            for category_name, service_list in services_dict.items():
                for svc in service_list:
                    service_id_src = svc.get("serviceId", "")
                    if not service_id_src:
                        continue

                    category_code = CATEGORY_MAP.get(category_name, "other")
                    schedule_type = SCHEDULE_TYPE_MAP.get(
                        svc.get("scheduleType", ""), "other"
                    )
                    day_code = svc.get("dayOfWeek")
                    time_start = svc.get("timeStart")
                    time_end = svc.get("timeEnd")
                    event_date = svc.get("eventDate")
                    notes_raw = svc.get("notes")

                    raw_name = svc.get("displayName", category_name)
                    display_name = sanitize_display_name(raw_name, category_code)
                    if display_name != raw_name:
                        sanitized_count += 1

                    lang_raw = svc.get("language")
                    language_code = LANGUAGE_MAP.get(lang_raw, None) if lang_raw else None

                    pattern_raw = svc.get("pattern")
                    pattern_code = PATTERN_MAP.get(pattern_raw, pattern_raw) if pattern_raw else None

                    relation_code = svc.get("timeRelation")
                    reference_service = svc.get("referenceService")
                    offset_minutes = svc.get("offsetMinutes")
                    location = svc.get("location")

                    service_rows.append((
                        church_id, service_id_src, category_code,
                        schedule_type, day_code, time_start, time_end,
                        event_date, language_code, pattern_code,
                        relation_code, reference_service, offset_minutes,
                        location, display_name, notes_raw
                    ))

    if dry_run:
        print(f"  [OK] Services validated: {len(service_rows)} ({sanitized_count} names sanitized)")
        return

    SERVICE_SQL = """
        INSERT INTO service (
            church_id, source_service_id, category_code,
            schedule_type_code, day_code, time_start, time_end,
            event_date, language_code, pattern_code,
            relation_code, reference_service, offset_minutes,
            location, display_name, notes_raw
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            category_code = VALUES(category_code),
            schedule_type_code = VALUES(schedule_type_code),
            day_code = VALUES(day_code),
            time_start = VALUES(time_start),
            time_end = VALUES(time_end),
            event_date = VALUES(event_date),
            language_code = VALUES(language_code),
            pattern_code = VALUES(pattern_code),
            relation_code = VALUES(relation_code),
            reference_service = VALUES(reference_service),
            offset_minutes = VALUES(offset_minutes),
            location = VALUES(location),
            display_name = VALUES(display_name),
            notes_raw = VALUES(notes_raw)
    """

    for i in range(0, len(service_rows), BATCH_SIZE):
        cur.executemany(SERVICE_SQL, service_rows[i:i + BATCH_SIZE])

    print(f"  [OK] Services upserted: {len(service_rows)} ({sanitized_count} names sanitized)")


# ---------------------------------------------------------------------------
# Bulletin loading
# ---------------------------------------------------------------------------
def load_bulletins(cur, state_dir, state_code, slug_to_id, dry_run=False):
    """Load bulletin_names.csv for one state using batched inserts."""
    csv_path = os.path.join(state_dir, "bulletin_names.csv")
    if not os.path.exists(csv_path):
        print(f"  [--] No bulletin_names.csv for {state_code}")
        return

    churches_data = {}
    names_total = 0
    names_skipped = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            slug = row.get("church_slug", "")
            if not slug or slug not in slug_to_id:
                names_skipped += 1
                continue

            person_name = row.get("person_name", "")
            if not person_name:
                names_skipped += 1
                continue

            names_total += 1

            if slug not in churches_data:
                churches_data[slug] = {
                    "church_url": row.get("church_url", ""),
                    "pdfs": {}
                }

            pdf_url = row.get("pdf_url", "")
            pdf_file = row.get("pdf_file", "")
            pdf_key = pdf_url or pdf_file

            if pdf_key not in churches_data[slug]["pdfs"]:
                churches_data[slug]["pdfs"][pdf_key] = {
                    "pdf_url": pdf_url,
                    "pdf_date": row.get("pdf_date") or None,
                    "pdf_file": pdf_file,
                    "names": []
                }

            churches_data[slug]["pdfs"][pdf_key]["names"].append({
                "person_name": person_name,
                "title": row.get("title", ""),
                "first_name": row.get("first_name", ""),
                "middle_name": row.get("middle_name", ""),
                "last_name": row.get("last_name", ""),
                "category": row.get("category", ""),
                "confidence": row.get("confidence", ""),
                "context": row.get("context", ""),
            })

    if dry_run:
        print(f"  [OK] Bulletin names validated: {names_total}, skipped: {names_skipped}")
        return

    # Phase 2: Batch insert sources
    source_rows = []
    for slug, data in churches_data.items():
        church_id = slug_to_id[slug]
        source_rows.append((church_id, data["church_url"], "csv_import"))

    if source_rows:
        for i in range(0, len(source_rows), BATCH_SIZE):
            cur.executemany(
                "INSERT IGNORE INTO bulletin_source (church_id, bulletin_page_url, discovery_source) VALUES (%s, %s, %s)",
                source_rows[i:i + BATCH_SIZE]
            )

    # Build source_id cache
    cid_to_slug = {slug_to_id[slug]: slug for slug in churches_data}
    church_ids = list(cid_to_slug.keys())
    source_cache = {}
    if church_ids:
        for i in range(0, len(church_ids), BATCH_SIZE):
            batch_ids = church_ids[i:i + BATCH_SIZE]
            placeholders = ",".join(["%s"] * len(batch_ids))
            cur.execute(
                f"SELECT church_id, bulletin_source_id FROM bulletin_source WHERE church_id IN ({placeholders})",
                batch_ids
            )
            for row in cur.fetchall():
                slug = cid_to_slug.get(row["church_id"])
                if slug:
                    source_cache[slug] = row["bulletin_source_id"]

    # Phase 3: Batch insert PDFs
    pdf_rows = []
    for slug, data in churches_data.items():
        if slug not in source_cache:
            continue
        source_id = source_cache[slug]
        for pdf_key, pdf_data in data["pdfs"].items():
            pdf_rows.append((
                source_id, pdf_data["pdf_url"], pdf_data["pdf_date"],
                pdf_data["pdf_file"], 1
            ))

    for i in range(0, len(pdf_rows), BATCH_SIZE):
        cur.executemany(
            "INSERT IGNORE INTO bulletin_pdf (bulletin_source_id, pdf_url, pdf_date, local_filename, text_extracted) VALUES (%s, %s, %s, %s, %s)",
            pdf_rows[i:i + BATCH_SIZE]
        )

    # Build pdf_id cache
    pdf_cache = {}
    source_ids = list(set(source_cache.values()))
    if source_ids:
        sid_to_slug = {sid: slug for slug, sid in source_cache.items()}
        for i in range(0, len(source_ids), BATCH_SIZE):
            batch_sids = source_ids[i:i + BATCH_SIZE]
            placeholders = ",".join(["%s"] * len(batch_sids))
            cur.execute(
                f"SELECT bulletin_pdf_id, bulletin_source_id, pdf_url, local_filename FROM bulletin_pdf WHERE bulletin_source_id IN ({placeholders})",
                batch_sids
            )
            for row in cur.fetchall():
                slug = sid_to_slug.get(row["bulletin_source_id"])
                if not slug:
                    continue
                pdf_key = row["pdf_url"] or row["local_filename"] or ""
                pdf_cache[(slug, pdf_key)] = row["bulletin_pdf_id"]

    # Phase 4: Batch insert names
    name_rows = []
    names_loaded = 0
    for slug, data in churches_data.items():
        for pdf_key, pdf_data in data["pdfs"].items():
            cache_key = (slug, pdf_key)
            if cache_key not in pdf_cache:
                names_skipped += len(pdf_data["names"])
                continue
            pdf_id = pdf_cache[cache_key]
            for n in pdf_data["names"]:
                name_rows.append((
                    pdf_id, n["person_name"], n["title"], n["first_name"],
                    n["middle_name"], n["last_name"], n["category"],
                    n["confidence"], n["context"]
                ))
                names_loaded += 1

    for i in range(0, len(name_rows), BATCH_SIZE):
        cur.executemany("""
            INSERT IGNORE INTO bulletin_name (
                bulletin_pdf_id, person_name, title, first_name,
                middle_name, last_name, category, confidence, context
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, name_rows[i:i + BATCH_SIZE])

    print(f"  [OK] Bulletin names upserted: {names_loaded}, skipped: {names_skipped}")


# ---------------------------------------------------------------------------
# Scrape log
# ---------------------------------------------------------------------------
def log_scrape_run(cur, states_processed, states_failed, elapsed_seconds):
    """Write an entry to scrape_log for audit trail."""
    notes = (
        f"States OK: {len(states_processed)}, Failed: {len(states_failed)}"
        + (f" [{', '.join(states_failed)}]" if states_failed else "")
    )
    errors = ", ".join(states_failed) if states_failed else None

    cur.execute("""
        INSERT INTO scrape_log (
            scrape_type, completed_at, status,
            communities_scraped, churches_scraped, services_upserted,
            errors, notes
        ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
    """, (
        "weekly_pipeline",
        "completed" if not states_failed else "partial",
        len(states_processed),
        None,
        None,
        errors,
        notes,
    ))


# ---------------------------------------------------------------------------
# State sync
# ---------------------------------------------------------------------------
def sync_state(conn, state_dir_name, dry_run=False, skip_bulletins=False):
    """Sync all data for one state to db99."""
    state_code = DIR_TO_STATE_CODE.get(state_dir_name)
    if not state_code:
        print(f"[--] Unknown state directory: {state_dir_name}")
        return False

    state_dir = os.path.join(DATA_DIR, state_dir_name)
    if not os.path.isdir(state_dir):
        print(f"[--] Directory not found: {state_dir}")
        return False

    print(f"\n{'='*60}")
    print(f"  Syncing: {STATE_CODE_TO_NAME.get(state_code, state_code)} ({state_code})")
    print(f"{'='*60}")

    cur = conn.cursor()
    start_time = time.time()

    try:
        slug_to_id = load_churches(cur, state_dir, state_code, dry_run)
        load_services(cur, state_dir, state_code, slug_to_id, dry_run)

        if not skip_bulletins:
            load_bulletins(cur, state_dir, state_code, slug_to_id, dry_run)
        else:
            print("  [--] Bulletins skipped")

        if not dry_run:
            conn.commit()
            elapsed = time.time() - start_time
            print(f"  [OK] {state_code} committed in {elapsed:.1f}s")
        else:
            conn.rollback()
            elapsed = time.time() - start_time
            print(f"  [OK] {state_code} validated (dry-run) in {elapsed:.1f}s")

        return True

    except Exception as e:
        conn.rollback()
        print(f"  [ERR] {state_code} failed, rolled back: {e}")
        return False
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Sync church data to db99")
    parser.add_argument("--state", type=str, help="Sync one state (e.g. 'ohio')")
    parser.add_argument("--dry-run", action="store_true", help="Validate without inserting")
    parser.add_argument("--skip-bulletins", action="store_true", help="Skip bulletin data")
    args = parser.parse_args()

    print("=" * 60)
    print("  Church Scrapes -> db99 Sync")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"  Target: db99.rds.blockshopper.com / church_scrapes")
    print("=" * 60)

    conn = get_connection()
    cur = conn.cursor()

    # Ensure all states in lookup table
    rows = [(code, name) for code, name in STATE_CODE_TO_NAME.items()]
    cur.executemany(
        "INSERT IGNORE INTO lk_state (state_code, state_name) VALUES (%s, %s)",
        rows
    )
    conn.commit()
    cur.close()

    if args.state:
        states = [args.state.lower().replace(" ", "_")]
    else:
        states = sorted([
            d for d in os.listdir(DATA_DIR)
            if os.path.isdir(os.path.join(DATA_DIR, d)) and d in DIR_TO_STATE_CODE
        ])

    print(f"\nStates to sync: {len(states)}")

    total_start = time.time()
    success_states = []
    failed_states = []

    for i, state_name in enumerate(states, 1):
        print(f"\n[{i}/{len(states)}]", end="")
        if sync_state(conn, state_name, args.dry_run, args.skip_bulletins):
            success_states.append(state_name)
        else:
            failed_states.append(state_name)

    total_elapsed = time.time() - total_start

    # Log the run
    if not args.dry_run:
        cur = conn.cursor()
        try:
            log_scrape_run(cur, success_states, failed_states, total_elapsed)
            conn.commit()
        except Exception as e:
            print(f"[--] Could not log scrape run: {e}")
        cur.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"  SYNC SUMMARY")
    print(f"{'='*60}")
    print(f"  States: {len(success_states)} succeeded, {len(failed_states)} failed")
    print(f"  Total time: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")

    if not args.dry_run:
        cur = conn.cursor()
        for table in ["church", "service", "bulletin_source", "bulletin_pdf", "bulletin_name"]:
            cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            count = cur.fetchone()["cnt"]
            print(f"  {table}: {count:,} rows")

        # Refresh the pre-computed bulletin stats table used by the dashboard
        print("\n  Refreshing bulletin_state_stats (high confidence only)...")
        try:
            cur.execute("TRUNCATE TABLE bulletin_state_stats")
            cur.execute("""
                INSERT INTO bulletin_state_stats
                    (state_code, total_names, unique_names, church_count, city_count)
                SELECT c.state_code,
                       COUNT(*) AS total_names,
                       COUNT(DISTINCT bn.person_name) AS unique_names,
                       COUNT(DISTINCT bs.church_id) AS church_count,
                       COUNT(DISTINCT c.city) AS city_count
                FROM bulletin_name bn
                JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
                JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
                JOIN church c ON bs.church_id = c.church_id
                WHERE bn.confidence = 'high'
                  AND bn.is_suspect = 0
                GROUP BY c.state_code
            """)
            conn.commit()
            print("  [OK] bulletin_state_stats refreshed")
        except Exception as e:
            print(f"  [--] Failed to refresh bulletin_state_stats: {e}")
        cur.close()

    conn.close()
    print("\n[OK] Done!")
    return 0 if not failed_states else 1


if __name__ == "__main__":
    sys.exit(main())
