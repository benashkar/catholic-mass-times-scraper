"""
Pipeline health checks — run after sync/rescore to validate data quality.

Usage:
    python tests/test_pipeline_health.py              # Full check
    python tests/test_pipeline_health.py --state OH   # One state
    python tests/test_pipeline_health.py --quick      # Just counts
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
        host=host, port=3306, user=user, password=password,
        database="church_scrapes", connect_timeout=10, autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def check_table_counts(cur):
    """Verify core tables have expected row counts."""
    print("\n1. TABLE COUNTS")
    tables = {
        "church": 20000,           # Expect 20K+ churches
        "service": 200000,         # Expect 200K+ services
        "bulletin_name": 1000000,  # Expect 1M+ names
        "bulletin_pdf": 200000,    # Expect 200K+ PDFs
        "bulletin_source": 4000,   # Expect 4K+ sources
        "bulletin_state_stats": 40,  # Expect 40+ states
        "ref_ssa_names": 90000,    # Expect 90K+ SSA names
        "ref_census_surnames": 150000,  # Expect 150K+ Census names
    }
    issues = []
    for table, min_expected in tables.items():
        try:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
            count = cur.fetchone()["cnt"]
            status = "[OK]" if count >= min_expected else "[WARN]"
            if count < min_expected:
                issues.append(f"{table}: {count:,} (expected {min_expected:,}+)")
            print(f"  {status} {table}: {count:,} rows (min: {min_expected:,})")
        except Exception as e:
            print(f"  [ERR] {table}: {e}")
            issues.append(f"{table}: query failed")
    return issues


def check_confidence_distribution(cur):
    """Verify confidence distribution is reasonable."""
    print("\n2. CONFIDENCE DISTRIBUTION")
    cur.execute("""
        SELECT confidence, COUNT(*) cnt,
               ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM bulletin_name), 1) AS pct
        FROM bulletin_name GROUP BY confidence ORDER BY confidence
    """)
    issues = []
    for r in cur.fetchall():
        conf = r["confidence"] or "(empty)"
        print(f"  {conf}: {r['cnt']:,} ({r['pct']}%)")
        # Flag if low is > 50% (something broke with scoring)
        if conf == "low" and r["pct"] > 50:
            issues.append(f"Low confidence is {r['pct']}% — scoring may be broken")
        # Flag if empty confidence exists
        if conf == "(empty)" and r["cnt"] > 1000:
            issues.append(f"{r['cnt']:,} names have no confidence score")
    return issues


def check_junk_rate_by_state(cur, state=None):
    """Check junk (low/suspect) rate per state — flag outliers."""
    print("\n3. JUNK RATE BY STATE")
    where = f"AND c.state_code = '{state}'" if state else ""
    cur.execute(f"""
        SELECT c.state_code,
               COUNT(*) AS total,
               SUM(CASE WHEN bn.confidence = 'low' OR bn.is_suspect = 1 THEN 1 ELSE 0 END) AS junk,
               ROUND(SUM(CASE WHEN bn.confidence = 'low' OR bn.is_suspect = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS junk_pct
        FROM bulletin_name bn
        JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
        JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
        JOIN church c ON bs.church_id = c.church_id
        {where}
        GROUP BY c.state_code
        HAVING junk_pct > 30
        ORDER BY junk_pct DESC
    """)
    issues = []
    rows = cur.fetchall()
    if rows:
        print("  States with >30% junk rate:")
        for r in rows:
            print(f"    {r['state_code']}: {r['junk_pct']}% junk ({r['junk']:,}/{r['total']:,})")
            if r["junk_pct"] > 50:
                issues.append(f"{r['state_code']} has {r['junk_pct']}% junk rate")
    else:
        print("  [OK] All states below 30% junk rate")
    return issues


def check_known_junk_still_present(cur):
    """Verify known junk terms are not in high/medium confidence."""
    print("\n4. KNOWN JUNK CHECK")
    junk_terms = [
        "Alzheimer", "Fish Fry", "Cancer", "Access Code", "Full Page",
        "Bulletin", "Sacrament", "Committee", "Receptionist",
    ]
    issues = []
    for term in junk_terms:
        cur.execute(
            "SELECT COUNT(*) cnt FROM bulletin_name "
            "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
            "AND person_name LIKE %s",
            (f"%{term}%",),
        )
        cnt = cur.fetchone()["cnt"]
        if cnt > 0:
            print(f"  [WARN] '{term}' still in {cnt} high/medium names")
            issues.append(f"'{term}' found in {cnt} high/medium names")
        else:
            print(f"  [OK] '{term}' cleaned")
    return issues


def check_lowercase_names(cur):
    """Verify no all-lowercase names in high/medium."""
    print("\n5. LOWERCASE NAME CHECK")
    cur.execute("""
        SELECT COUNT(*) cnt FROM bulletin_name
        WHERE confidence IN ('high','medium') AND is_suspect = 0
        AND person_name = BINARY LOWER(person_name) AND person_name REGEXP '^[a-z]'
    """)
    cnt = cur.fetchone()["cnt"]
    issues = []
    if cnt > 0:
        print(f"  [WARN] {cnt:,} all-lowercase names in high/medium")
        issues.append(f"{cnt:,} lowercase names still scored high/medium")
    else:
        print(f"  [OK] No all-lowercase names in high/medium")
    return issues


def check_scrape_recency(cur):
    """Check if scrapes are running on schedule."""
    print("\n6. SCRAPE RECENCY")
    cur.execute("""
        SELECT scrape_type, MAX(completed_at) AS last_run,
               TIMESTAMPDIFF(HOUR, MAX(completed_at), NOW()) AS hours_ago
        FROM scrape_log
        GROUP BY scrape_type
    """)
    issues = []
    rows = cur.fetchall()
    if not rows:
        print("  [WARN] No entries in scrape_log")
        issues.append("scrape_log is empty — pipeline may not be logging runs")
    else:
        for r in rows:
            hours = r["hours_ago"] or 999
            status = "[OK]" if hours < 48 else "[WARN]"
            print(f"  {status} {r['scrape_type']}: last run {r['last_run']} ({hours}h ago)")
            if hours > 48:
                issues.append(f"{r['scrape_type']} last ran {hours}h ago")
    return issues


def check_empty_names(cur):
    """Check for records with empty first or last names in high/medium."""
    print("\n7. EMPTY NAME FIELDS")
    cur.execute("""
        SELECT COUNT(*) cnt FROM bulletin_name
        WHERE confidence IN ('high','medium') AND is_suspect = 0
        AND (first_name = '' OR last_name = '')
    """)
    cnt = cur.fetchone()["cnt"]
    issues = []
    if cnt > 0:
        print(f"  [WARN] {cnt:,} high/medium names with empty first or last name")
        issues.append(f"{cnt:,} names missing first/last in high/medium")
    else:
        print(f"  [OK] All high/medium names have first and last")
    return issues


def main():
    parser = argparse.ArgumentParser(description="Pipeline health checks")
    parser.add_argument("--state", type=str, help="Check one state")
    parser.add_argument("--quick", action="store_true", help="Just table counts")
    args = parser.parse_args()

    print("=" * 60)
    print("  PIPELINE HEALTH CHECK")
    print("=" * 60)

    conn = get_connection()
    cur = conn.cursor()

    all_issues = []
    all_issues.extend(check_table_counts(cur))

    if not args.quick:
        all_issues.extend(check_confidence_distribution(cur))
        all_issues.extend(check_junk_rate_by_state(cur, args.state))
        all_issues.extend(check_known_junk_still_present(cur))
        all_issues.extend(check_lowercase_names(cur))
        all_issues.extend(check_scrape_recency(cur))
        all_issues.extend(check_empty_names(cur))

    conn.close()

    print(f"\n{'='*60}")
    if all_issues:
        print(f"  {len(all_issues)} ISSUES FOUND:")
        for issue in all_issues:
            print(f"    - {issue}")
        return 1
    else:
        print("  ALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
