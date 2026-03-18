"""
rescore_names_sql.py — Re-score bulletin names and remove junk using SQL on db99.

Runs after sync_to_db99.py to clean new data. Uses ref_ssa_names and
ref_census_surnames lookup tables already on db99.

Steps:
1. SQL rescore: UPDATE confidence based on SSA first name + Census last name match
2. Junk blocklist: Mark known non-names as low/suspect
3. Lowercase cleanup: Mark all-lowercase and lowercase-first names
4. Refresh bulletin_state_stats

Fast: ~30 seconds for all 1.3M names (all SQL, no Python loops).
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
        read_timeout=300,
        write_timeout=300,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rescore bulletin names")
    parser.add_argument("--cleanup-only", action="store_true",
                        help="Skip full rescore, just apply blocklist + cleanup + refresh stats")
    args = parser.parse_args()

    mode = "cleanup-only" if args.cleanup_only else "full"
    print(f"[OK] Starting SQL rescore (mode={mode})...")
    t = time.time()

    conn = get_connection()
    cur = conn.cursor()

    if not args.cleanup_only:
        # Step 1: SQL rescore using reference tables (SLOW — ~11 min on 2.6M rows)
        print("  Step 1: Rescore using ref_ssa_names + ref_census_surnames...")
        cur.execute("""
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
        """)
        print(f"    [OK] {cur.rowcount:,} rows rescored")
    else:
        print("  Step 1: SKIPPED (cleanup-only mode)")

    # Step 2: Junk blocklist
    print("  Step 2: Applying junk blocklist...")
    blocklist = [
        "Alzheimer", "Cancer", "Diabetes", "Parkinson", "Dementia", "Hospice",
        "Fish Fry", "Fish Frys", "Access Code", "Full Page",
        "Awareness Month", "Support Group",
        "Bulletin", "Stewardship", "Registration", "Sacrament",
        "Committee", "Office Hours", "Mass Schedule",
        "Holy Spirit", "Sacred Heart", "Holy Family", "Blessed Mother",
        "Corpus Christi", "Divine Mercy", "Blessed Sacrament", "Blessed Virgin",
        "Our Lady", "Infant Jesus",
        "Ice Cream", "Craft Fair", "Yard Sale", "Blood Drive", "Food Pantry",
        "Thrift Store", "Bake Sale", "Rummage Sale", "Garage Sale",
        "Forever Young", "Silver Angels", "Golden Agers",
        "Anointed", "Baptism", "Confirmation", "Communion",
        "Rosary", "Novena", "Liturgy", "Eucharist", "Adoration",
        "Lenten", "Advent", "Easter", "Christmas", "Pentecost",
        "Bible Study", "Prayer Group", "Prayer Chain",
        "Choir Director", "Music Director",
        "Pancake", "Spaghetti Dinner", "Pot Luck", "Potluck",
        "Knights Columbus", "Altar Society", "Ladies Guild",
        "Youth Group", "Young Adults",
        "Funeral Home", "Wedding Anniversary",
        "Volunteer", "Coordinator", "Administrator", "Custodian",
        "Receptionist", "Bookkeeper", "Secretary",
        "Phone Number", "Website", "Business Manager",
        "Pope Francis", "Pope Benedict", "Pope John", "Mother Teresa",
        "Saint Joseph", "Saint Patrick", "Saint Michael", "Saint Mary",
        "Helping Hands", "Good Shepherd",
        "Social Hall", "Parish Hall",
        "Gift Card", "Amazon Smile", "Online Giving",
        "Open House", "Welcome Back",
        "Spring Break", "Summer Camp", "Vacation Bible",
        "Fair Trade", "Saint Vincent", "de Paul",
        "Rehab ",
    ]
    blocked = 0
    for term in blocklist:
        cur.execute(
            "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
            "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
            "AND person_name LIKE %s",
            (f"%{term}%",),
        )
        blocked += cur.rowcount
    print(f"    [OK] {blocked:,} junk records blocked")

    # Step 3: Lowercase cleanup
    print("  Step 3: Removing lowercase names...")
    cur.execute("""
        UPDATE bulletin_name SET confidence = 'low', is_suspect = 1
        WHERE confidence IN ('high','medium') AND is_suspect = 0
        AND person_name = BINARY LOWER(person_name) AND person_name REGEXP '^[a-z]'
    """)
    lc1 = cur.rowcount
    cur.execute("""
        UPDATE bulletin_name SET confidence = 'low', is_suspect = 1
        WHERE confidence IN ('high','medium') AND is_suspect = 0
        AND first_name = BINARY LOWER(first_name) AND first_name != ''
        AND first_name REGEXP '^[a-z]'
    """)
    lc2 = cur.rowcount
    print(f"    [OK] {lc1 + lc2:,} lowercase names removed")

    # Step 4: Junk first/last words
    print("  Step 4: Removing junk first/last words...")
    junk_first = [
        "or", "and", "the", "for", "but", "not", "all", "any", "can", "has",
        "his", "her", "our", "who", "how", "may", "new", "old", "one", "two",
        "per", "via", "end", "set", "let", "got", "did", "get",
        "Wed", "Tue", "Tues", "Mon", "Thu", "Thur", "Fri", "Sat", "Sun",
        "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        "St", "Christ", "Jesus", "God", "Lord", "Holy",
    ]
    junk_last = [
        "Guild", "Bank", "Hall", "Team", "Club", "Group", "Board", "Drive",
        "Sale", "Store", "Fair", "Night", "Week", "Month", "Year",
        "Ridge", "Creek", "Lake", "Hill", "Valley", "Springs",
        "Church", "Parish", "Chapel", "Cathedral", "Basilica",
        "School", "Academy", "College", "University",
        "County", "City", "Town", "Village", "Center", "Centre",
        "Street", "Road", "Avenue", "Boulevard", "Lane", "Way",
        "Code", "Page", "Form", "Link", "Site", "Line",
    ]
    jw = 0
    for word in junk_first:
        cur.execute(
            "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
            "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
            "AND first_name = %s",
            (word,),
        )
        jw += cur.rowcount
    for word in junk_last:
        cur.execute(
            "UPDATE bulletin_name SET confidence = 'low', is_suspect = 1 "
            "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
            "AND last_name = %s",
            (word,),
        )
        jw += cur.rowcount
    print(f"    [OK] {jw:,} junk first/last words removed")

    # Step 5: Refresh stats
    print("  Step 5: Refreshing bulletin_state_stats...")
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
    print(f"    [OK] Stats refreshed")

    elapsed = time.time() - t
    print(f"\n[OK] Rescore complete in {elapsed:.0f}s")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
