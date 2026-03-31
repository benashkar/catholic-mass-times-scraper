"""
rescore_names_sql.py — Re-score bulletin names and remove junk using SQL on db99.

Runs after sync_to_db99.py to clean new data. Uses ref_ssa_names and
ref_census_surnames lookup tables already on db99.

Steps:
1. SQL rescore: UPDATE confidence based on SSA first name + Census last name match
2. Junk blocklist: Mark known non-names as low/suspect
3. Lowercase cleanup: Mark all-lowercase and lowercase-first names
4. Refresh bulletin_state_stats

All SQL, no Python loops. Use --new-only for incremental runs.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def get_connection():
    """Connect to db99."""
    import pymysql

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
        read_timeout=900,
        write_timeout=900,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Rescore bulletin names")
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Skip full rescore, just apply blocklist + cleanup + refresh stats",
    )
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="Only rescore names not yet scored (confidence IS NULL or 'unscored')",
    )
    args = parser.parse_args()

    mode = "cleanup-only" if args.cleanup_only else ("new-only" if args.new_only else "full")
    print(f"[OK] Starting SQL rescore (mode={mode})...")
    t = time.time()

    conn = get_connection()
    cur = conn.cursor()

    # Resolve watermark for new-only mode
    watermark = 0
    if args.new_only:
        cur.execute(
            "SELECT COALESCE(MAX(CAST(notes AS UNSIGNED)), 0) AS watermark "
            "FROM scrape_log WHERE scrape_type = 'rescore_sql_watermark'"
        )
        row = cur.fetchone()
        watermark = int(row["watermark"]) if row and row["watermark"] else 0
        if watermark > 0:
            print(f"  Rescoring names with bulletin_name_id > {watermark:,}")
        else:
            print("  No watermark found — rescoring all names")

    if not args.cleanup_only:
        # Step 1: SQL rescore using reference tables
        print("  Step 1: Rescore using ref_ssa_names + ref_census_surnames...")
        rescore_sql = """
            UPDATE bulletin_name bn
            LEFT JOIN ref_ssa_names ssa ON LOWER(
                CASE WHEN LOCATE(' ', bn.person_name) > 0
                     THEN SUBSTRING_INDEX(bn.person_name, ' ', 1)
                     ELSE bn.person_name
                END
            ) = ssa.name
            LEFT JOIN ref_census_surnames cen ON LOWER(
                CASE WHEN LOCATE(' ', bn.person_name) > 0
                     THEN SUBSTRING_INDEX(bn.person_name, ' ', -1)
                     ELSE ''
                END
            ) = cen.name
            SET bn.confidence = CASE
                WHEN ssa.name IS NOT NULL AND cen.name IS NOT NULL THEN 'high'
                WHEN ssa.name IS NOT NULL OR cen.name IS NOT NULL THEN 'medium'
                ELSE 'low'
            END,
            bn.is_suspect = CASE
                WHEN LOCATE(' ', TRIM(bn.person_name)) = 0 THEN 1
                WHEN LENGTH(bn.person_name) - LENGTH(REPLACE(bn.person_name, ' ', '')) >= 2
                     AND ssa.name IS NOT NULL
                     AND LOWER(SUBSTRING_INDEX(bn.person_name, ' ', -1)) IN (SELECT name FROM ref_ssa_names)
                     AND cen.name IS NULL
                THEN 1
                ELSE 0
            END
        """
        if watermark > 0:
            rescore_sql += " WHERE bn.bulletin_name_id > %s"
            cur.execute(rescore_sql, (watermark,))
        else:
            cur.execute(rescore_sql)
        print(f"    [OK] {cur.rowcount:,} rows rescored")
    else:
        print("  Step 1: SKIPPED (cleanup-only mode)")

    # Step 2: Junk blocklist (always runs on all medium+high, not just new — catches edge cases)
    print("  Step 2: Applying junk blocklist...")
    # Load blocklist from config/blocklist.csv (externalized for easy editing)
    blocklist_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "blocklist.csv")
    blocklist = []
    with open(blocklist_path, "r", encoding="utf-8") as f:
        import csv
        reader = csv.DictReader(f)
        for row in reader:
            blocklist.append(row["term"])
    like_clauses = " OR ".join(["person_name LIKE %s"] * len(blocklist))
    like_params = [f"%{term}%" for term in blocklist]
    block_sql = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        f"AND ({like_clauses})"
    )
    if watermark > 0:
        block_sql += " AND bulletin_name_id > %s"
        like_params.append(watermark)
    cur.execute(block_sql, like_params)
    blocked = cur.rowcount
    print(f"    [OK] {blocked:,} junk records blocked")

    # Step 3: Lowercase cleanup
    print("  Step 3: Removing lowercase names...")
    wm_clause = " AND bulletin_name_id > %s" if watermark > 0 else ""
    wm_params = [watermark] if watermark > 0 else []
    cur.execute(
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        "AND person_name = BINARY LOWER(person_name) AND person_name REGEXP '^[a-z]'" + wm_clause,
        wm_params,
    )
    lc1 = cur.rowcount
    cur.execute(
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        "AND first_name = BINARY LOWER(first_name) AND first_name != '' "
        "AND first_name REGEXP '^[a-z]'" + wm_clause,
        wm_params,
    )
    lc2 = cur.rowcount
    print(f"    [OK] {lc1 + lc2:,} lowercase names removed")

    # Step 4: Junk first/last words
    print("  Step 4: Removing junk first/last words...")
    junk_first = [
        "or",
        "and",
        "the",
        "for",
        "but",
        "not",
        "all",
        "any",
        "can",
        "has",
        "his",
        "her",
        "our",
        "who",
        "how",
        "may",
        "new",
        "old",
        "one",
        "two",
        "per",
        "via",
        "end",
        "set",
        "let",
        "got",
        "did",
        "get",
        "Wed",
        "Tue",
        "Tues",
        "Mon",
        "Thu",
        "Thur",
        "Fri",
        "Sat",
        "Sun",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
        "St",
        "Christ",
        "Jesus",
        "God",
        "Lord",
        "Holy",
        # Full day names
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "SUNDAY",
        "SATURDAY",
        # Full month names (May excluded — common first name)
        "January",
        "February",
        "March",
        "April",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "JANUARY",
        "FEBRUARY",
        "JULY",
        # Plural day names
        "Mondays",
        "Tuesdays",
        "Wednesdays",
        "Thursdays",
        "Fridays",
        "Saturdays",
        "Sundays",
        # Other non-name starters
        "Every",
        "Each",
        "Holiday",
        "Holidays",
        "Birthday",
        "Birthdays",
        "Today",
        "Everyday",
        "Weekdays",
        "Day",
        "Days",
    ]
    junk_last = [
        "Guild",
        "Bank",
        "Hall",
        "Team",
        "Club",
        "Group",
        "Board",
        "Drive",
        "Sale",
        "Store",
        "Fair",
        "Night",
        "Week",
        "Month",
        "Year",
        "Ridge",
        "Creek",
        "Lake",
        "Hill",
        "Valley",
        "Springs",
        "Church",
        "Parish",
        "Chapel",
        "Cathedral",
        "Basilica",
        "School",
        "Academy",
        "College",
        "University",
        "County",
        "City",
        "Town",
        "Village",
        "Center",
        "Centre",
        "Street",
        "Road",
        "Avenue",
        "Boulevard",
        "Lane",
        "Way",
        "Code",
        "Page",
        "Form",
        "Link",
        "Site",
        "Line",
        # Contact/address artifacts
        "Email",
        "Fax",
        "Phone",
        # Organization/event suffixes
        "Press",
        "Institute",
        "Station",
        "Court",
        "People",
        "Flower",
        "Missal",
        "Gras",
        "Confession",
        "Evenings",
        "Mornings",
        "Throw",
        "Meditation",
        "Direction",
        "Residence",
        "Times",
        "App",
        "Day",
    ]
    first_in = ",".join(["%s"] * len(junk_first))
    last_in = ",".join(["%s"] * len(junk_last))
    jw_sql_first = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        f"WHERE confidence IN ('high','medium') AND is_suspect = 0 AND first_name IN ({first_in})"
    )
    jw_sql_last = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        f"WHERE confidence IN ('high','medium') AND is_suspect = 0 AND last_name IN ({last_in})"
    )
    if watermark > 0:
        jw_sql_first += " AND bulletin_name_id > %s"
        jw_sql_last += " AND bulletin_name_id > %s"
        cur.execute(jw_sql_first, junk_first + [watermark])
        jw = cur.rowcount
        cur.execute(jw_sql_last, junk_last + [watermark])
        jw += cur.rowcount
    else:
        cur.execute(jw_sql_first, junk_first)
        jw = cur.rowcount
        cur.execute(jw_sql_last, junk_last)
        jw += cur.rowcount
    print(f"    [OK] {jw:,} junk first/last words removed")

    # Step 5: Downgrade empty first/last name records
    print("  Step 5: Downgrading names with empty first or last name...")
    empty_sql = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        "AND (first_name = '' OR last_name = '')"
    )
    if watermark > 0:
        empty_sql += " AND bulletin_name_id > %s"
        cur.execute(empty_sql, (watermark,))
    else:
        cur.execute(empty_sql)
    empty_cnt = cur.rowcount
    print(f"    [OK] {empty_cnt:,} empty-name records downgraded")

    # Step 6: ALL CAPS cleanup
    print("  Step 6: Downgrading ALL CAPS names...")
    caps_sql = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        "AND BINARY person_name = UPPER(person_name) AND LENGTH(person_name) > 3"
    )
    if watermark > 0:
        caps_sql += " AND bulletin_name_id > %s"
        cur.execute(caps_sql, (watermark,))
    else:
        cur.execute(caps_sql)
    caps_cnt = cur.rowcount
    print(f"    [OK] {caps_cnt:,} ALL CAPS records downgraded")

    # Step 7: Newline artifact cleanup
    print("  Step 7: Downgrading newline artifacts...")
    nl_sql = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        "AND person_name LIKE '%\n%'"
    )
    if watermark > 0:
        nl_sql += " AND bulletin_name_id > %s"
        cur.execute(nl_sql, (watermark,))
    else:
        cur.execute(nl_sql)
    nl_cnt = cur.rowcount
    print(f"    [OK] {nl_cnt:,} newline artifacts downgraded")

    # Step 8: Single-initial last name cleanup
    print("  Step 8: Downgrading single-initial last names...")
    init_sql = (
        "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
        "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
        "AND LENGTH(last_name) = 1"
    )
    if watermark > 0:
        init_sql += " AND bulletin_name_id > %s"
        cur.execute(init_sql, (watermark,))
    else:
        cur.execute(init_sql)
    init_cnt = cur.rowcount
    print(f"    [OK] {init_cnt:,} single-initial last names downgraded")

    # Step 9: Refresh stats
    print("  Step 9: Refreshing bulletin_state_stats...")
    cur.execute("TRUNCATE TABLE bulletin_state_stats")
    cur.execute("""
        INSERT INTO bulletin_state_stats (state_code, total_names, unique_names, church_count, city_count)
        SELECT c.state_code,
               COUNT(DISTINCT CONCAT(bn.first_name, '|', bn.last_name, '|', c.name, '|', c.city)) AS total_names,
               COUNT(DISTINCT CONCAT(bn.first_name, '|', bn.last_name)) AS unique_names,
               COUNT(DISTINCT bs.church_id) AS church_count,
               COUNT(DISTINCT c.city) AS city_count
        FROM bulletin_name bn
        JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
        JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
        JOIN church c ON bs.church_id = c.church_id
        WHERE bn.confidence IN ('high', 'medium') AND bn.is_suspect = 0
          AND bn.first_name != '' AND bn.last_name != ''
        GROUP BY c.state_code
    """)
    print("    [OK] Stats refreshed")

    elapsed = time.time() - t

    # Log the run
    cur.execute(
        """
        INSERT INTO scrape_log (scrape_type, completed_at, status, notes)
        VALUES ('rescore_sql', NOW(), 'completed', %s)
    """,
        (f"mode={mode} in {elapsed:.0f}s",),
    )

    # Save watermark (max bulletin_name_id) for --new-only next run
    if not args.cleanup_only:
        cur.execute("SELECT MAX(bulletin_name_id) AS max_id FROM bulletin_name")
        max_id = cur.fetchone()["max_id"] or 0
        cur.execute(
            """
            INSERT INTO scrape_log (scrape_type, completed_at, status, notes)
            VALUES ('rescore_sql_watermark', NOW(), 'completed', %s)
        """,
            (str(max_id),),
        )

    print(f"\n[OK] Rescore complete in {int(elapsed)}s")

    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback

        traceback.print_exc()
        # Log the error to scrape_log so we can see it from the database
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO scrape_log (scrape_type, completed_at, status, errors) "
                "VALUES ('rescore_sql', NOW(), 'failed', %s)",
                (f"{type(e).__name__}: {e}"[:500],),
            )
            conn.close()
        except Exception:
            pass
        sys.exit(1)
