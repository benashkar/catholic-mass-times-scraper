"""
backfill_empty_fields.py — Layer 2 self-healing: fill empty fields from existing data.

Queries bulletin_name rows with missing critical fields (first_name, last_name,
category, role) and re-parses from person_name and context columns. No HTTP
requests — pure regex on existing DB data. Target: under 60 seconds.

Usage:
    python backfill_empty_fields.py              # Backfill all (limit 50K)
    python backfill_empty_fields.py --limit 100  # Test with small batch
    python backfill_empty_fields.py --dry-run    # Show counts without updating
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.parsers.fallback_parsers import (
    parse_category_from_context,
    parse_first_last_from_person_name,
    parse_role_from_context,
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
        connect_timeout=30,
        read_timeout=300,
        write_timeout=300,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def backfill_names(cur, limit=50000, dry_run=False):
    """Backfill empty first_name/last_name from person_name."""
    cur.execute(
        """
        SELECT bulletin_name_id, person_name, first_name, last_name
        FROM bulletin_name
        WHERE (first_name = '' OR last_name = '')
          AND confidence IN ('high', 'medium')
          AND is_suspect = 0
          AND person_name != ''
        LIMIT %s
    """,
        (limit,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  No names need backfill.")
        return 0

    print(f"  Found {len(rows)} names with empty first/last name")
    if dry_run:
        return len(rows)

    updated = 0
    for row in rows:
        fb = parse_first_last_from_person_name(row["person_name"])
        new_first = fb.get("first_name", "")
        new_last = fb.get("last_name", "")

        # Only update fields that are currently empty
        updates = []
        params = []
        if not row["first_name"] and new_first:
            updates.append("first_name = %s")
            params.append(new_first[:50])
        if not row["last_name"] and new_last:
            updates.append("last_name = %s")
            params.append(new_last[:50])

        if updates:
            params.append(row["bulletin_name_id"])
            cur.execute(
                f"UPDATE bulletin_name SET {', '.join(updates)} WHERE bulletin_name_id = %s",
                params,
            )
            updated += cur.rowcount

    return updated


def backfill_categories(cur, limit=50000, dry_run=False):
    """Backfill empty category from context text."""
    cur.execute(
        """
        SELECT bulletin_name_id, context
        FROM bulletin_name
        WHERE category = ''
          AND confidence IN ('high', 'medium')
          AND is_suspect = 0
          AND context != ''
        LIMIT %s
    """,
        (limit,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  No categories need backfill.")
        return 0

    print(f"  Found {len(rows)} names with empty category")
    if dry_run:
        return len(rows)

    updated = 0
    for row in rows:
        category = parse_category_from_context(row["context"])
        if category:
            cur.execute(
                "UPDATE bulletin_name SET category = %s WHERE bulletin_name_id = %s",
                (category[:30], row["bulletin_name_id"]),
            )
            updated += cur.rowcount

    return updated


def backfill_roles(cur, limit=50000, dry_run=False):
    """Backfill empty role from context text."""
    cur.execute(
        """
        SELECT bulletin_name_id, context
        FROM bulletin_name
        WHERE role = ''
          AND confidence IN ('high', 'medium')
          AND is_suspect = 0
          AND context != ''
        LIMIT %s
    """,
        (limit,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  No roles need backfill.")
        return 0

    print(f"  Found {len(rows)} names with empty role")
    if dry_run:
        return len(rows)

    updated = 0
    for row in rows:
        role = parse_role_from_context(row["context"])
        if role:
            cur.execute(
                "UPDATE bulletin_name SET role = %s WHERE bulletin_name_id = %s",
                (role[:100], row["bulletin_name_id"]),
            )
            updated += cur.rowcount

    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill empty bulletin_name fields")
    parser.add_argument(
        "--limit", type=int, default=50000, help="Max rows per field (default 50000)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show counts without updating")
    args = parser.parse_args()

    print("=" * 60)
    print("  BACKFILL EMPTY FIELDS (Layer 2 Self-Healing)")
    print("=" * 60)

    start = time.time()
    conn = get_connection()
    cur = conn.cursor()

    n_names = backfill_names(cur, args.limit, args.dry_run)
    n_cats = backfill_categories(cur, args.limit, args.dry_run)
    n_roles = backfill_roles(cur, args.limit, args.dry_run)

    elapsed = time.time() - start

    # Log to scrape_log
    if not args.dry_run:
        try:
            cur.execute(
                "INSERT INTO scrape_log (scrape_type, completed_at, status, notes) "
                "VALUES ('backfill', NOW(), 'completed', %s)",
                (f"names={n_names} categories={n_cats} roles={n_roles} in {elapsed:.0f}s",),
            )
        except Exception:
            pass

    conn.close()

    label = "DRY RUN — would update" if args.dry_run else "Updated"
    print(f"\n  {label}: {n_names} names, {n_cats} categories, {n_roles} roles")
    print(f"  Time: {elapsed:.1f}s")
    print("[OK] Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
