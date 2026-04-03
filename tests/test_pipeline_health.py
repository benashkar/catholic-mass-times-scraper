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

from src.utils.health_checks import (
    check_backfill_coverage,
    check_confidence_distribution,
    check_empty_names,
    check_junk_rate_by_state,
    check_known_junk_still_present,
    check_lowercase_names,
    check_scrape_recency,
    check_table_counts,
)


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
        connect_timeout=10,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def print_check(name, result):
    """Print a check result in human-readable format."""
    print(f"\n{name}")
    issues = result.get("issues", [])

    # Print data details based on check type
    if "counts" in result:
        for table, count in result["counts"].items():
            status = "[OK]" if count and count >= 1000 else "[WARN]"
            print(f"  {status} {table}: {count:,}" if count else f"  [ERR] {table}: unavailable")
    if "distribution" in result:
        for conf, data in result["distribution"].items():
            print(f"  {conf}: {data['count']:,} ({data['pct']}%)")
    if "flagged_states" in result:
        if result["flagged_states"]:
            print("  States with >30% junk rate:")
            for s in result["flagged_states"]:
                print(f"    {s['state']}: {s['junk_pct']}% junk ({s['junk']:,}/{s['total']:,})")
        else:
            print("  [OK] All states below 30% junk rate")
    if "found" in result:
        if result["found"]:
            for term, cnt in result["found"].items():
                print(f"  [WARN] '{term}' still in {cnt} high/medium names")
        else:
            print("  [OK] All known junk terms cleaned")
    if "count" in result and "distribution" not in result:
        cnt = result["count"]
        status = "[WARN]" if cnt > 0 else "[OK]"
        print(f"  {status} {cnt:,} affected rows")
    if "recency" in result:
        for stype, data in result["recency"].items():
            hours = data["hours_ago"]
            status = "[OK]" if hours < 48 else "[WARN]"
            print(f"  {status} {stype}: last run {data['last_run']} ({hours}h ago)")

    if issues:
        for issue in issues:
            print(f"  [ISSUE] {issue}")


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

    result = check_table_counts(cur)
    print_check("1. TABLE COUNTS", result)
    all_issues.extend(result["issues"])

    if not args.quick:
        result = check_confidence_distribution(cur)
        print_check("2. CONFIDENCE DISTRIBUTION", result)
        all_issues.extend(result["issues"])

        result = check_junk_rate_by_state(cur, args.state)
        print_check("3. JUNK RATE BY STATE", result)
        all_issues.extend(result["issues"])

        result = check_known_junk_still_present(cur)
        print_check("4. KNOWN JUNK CHECK", result)
        all_issues.extend(result["issues"])

        result = check_lowercase_names(cur)
        print_check("5. LOWERCASE NAME CHECK", result)
        all_issues.extend(result["issues"])

        result = check_scrape_recency(cur)
        print_check("6. SCRAPE RECENCY", result)
        all_issues.extend(result["issues"])

        result = check_empty_names(cur)
        print_check("7. EMPTY NAME FIELDS", result)
        all_issues.extend(result["issues"])

        result = check_backfill_coverage(cur)
        print_check("8. BACKFILL COVERAGE", result)
        print(f"  Empty name parts: {result['empty_name_parts']:,}")
        print(f"  Empty category: {result['empty_category']:,}")
        print(f"  Empty role: {result['empty_role']:,}")
        print(f"  Total high/medium: {result['total_high_medium']:,}")

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
