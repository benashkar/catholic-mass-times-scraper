"""
rescore_with_ner.py — Re-score existing bulletin names with NER veto gate + couple detection.

One-time cleanup script (Phase 4). Reads existing high/medium confidence names
from db99, runs spaCy NER + probablepeople couple detection, and updates scores.

Designed to run on Render (Python 3.12 + spaCy en_core_web_lg) since spaCy
doesn't support Python 3.14.

Usage:
    python rescore_with_ner.py                     # All states
    python rescore_with_ner.py --state GA           # One state (good for testing)
    python rescore_with_ner.py --limit 1000         # First 1000 names
    python rescore_with_ner.py --dry-run             # Show what would change, don't write
    python rescore_with_ner.py --batch-size 500     # Names per batch (default 500)
    python rescore_with_ner.py --couples-only        # Just detect/split couples
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql

from run_bulletin_scraper import (
    confidence_label,
    detect_couple,
    ner_veto_batch,
    parse_name_parts,
    score_name_confidence,
)


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
        read_timeout=600,
        write_timeout=600,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ---------------------------------------------------------------------------
# NER veto pass
# ---------------------------------------------------------------------------


def run_ner_veto_pass(cur, args):
    """Run NER veto on all high/medium confidence names.

    Names that NER doesn't recognize as PERSON get downgraded to low + suspect.
    """
    print("\n  Phase A: NER Veto Gate")
    print("  " + "-" * 50)

    # Build query for names to check
    where = "WHERE bn.confidence IN ('high', 'medium') AND bn.is_suspect = 0"
    params = []

    if args.state:
        where += """
            AND bn.bulletin_pdf_id IN (
                SELECT bp.bulletin_pdf_id FROM bulletin_pdf bp
                JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id
                JOIN church c ON c.church_id = bs.church_id
                WHERE c.state_code = %s
            )
        """
        params.append(args.state.upper())

    # Count total
    cur.execute(f"SELECT COUNT(*) AS cnt FROM bulletin_name bn {where}", params)
    total = cur.fetchone()["cnt"]
    print(f"  Names to check: {total:,}")

    if total == 0:
        return {"checked": 0, "downgraded": 0}

    limit_clause = f"LIMIT {args.limit}" if args.limit else ""

    # Process in batches
    batch_size = args.batch_size
    offset = 0
    checked = 0
    downgraded = 0
    start = time.time()

    while True:
        cur.execute(
            f"""
            SELECT bn.bulletin_name_id, bn.person_name, bn.context
            FROM bulletin_name bn
            {where}
            ORDER BY bn.bulletin_name_id
            LIMIT %s OFFSET %s
        """,
            params + [batch_size, offset],
        )

        rows = cur.fetchall()
        if not rows:
            break

        # Run NER veto in batch
        names = [r["person_name"] for r in rows]
        contexts = [r["context"] or "" for r in rows]
        ner_results = ner_veto_batch(names, contexts)

        # Update names that failed NER
        ids_to_downgrade = []
        for row, ner_pass in zip(rows, ner_results):
            if not ner_pass:
                ids_to_downgrade.append(row["bulletin_name_id"])

        if ids_to_downgrade and not args.dry_run:
            # Batch update
            placeholders = ",".join(["%s"] * len(ids_to_downgrade))
            cur.execute(
                f"""
                UPDATE bulletin_name
                SET confidence = 'low', is_suspect = 1
                WHERE bulletin_name_id IN ({placeholders})
            """,
                ids_to_downgrade,
            )

        checked += len(rows)
        downgraded += len(ids_to_downgrade)
        offset += batch_size

        # Progress
        elapsed = time.time() - start
        rate = checked / elapsed if elapsed > 0 else 0
        remaining = (total - checked) / rate if rate > 0 else 0
        pct = checked / total * 100 if total > 0 else 100
        dr_label = " (dry-run)" if args.dry_run else ""
        print(
            f"    [{checked:,}/{total:,}] ({pct:.0f}%) "
            f"downgraded={downgraded:,}{dr_label} "
            f"({rate:.0f}/s, ~{remaining/60:.0f}min left)",
            flush=True,
        )

        if args.limit and checked >= args.limit:
            break

    elapsed = time.time() - start
    print(f"  NER veto complete: {checked:,} checked, {downgraded:,} downgraded in {elapsed:.0f}s")
    return {"checked": checked, "downgraded": downgraded}


# ---------------------------------------------------------------------------
# Couple detection pass
# ---------------------------------------------------------------------------


def run_couple_detection(cur, args):
    """Detect and split couple names ("John & Mary Smith" → two records).

    Looks for names with "&", "and", or 3+ words where first two are
    gendered names (one male, one female) sharing a surname.
    """
    print("\n  Phase B: Couple Detection")
    print("  " + "-" * 50)

    # Find candidate couple names: contain "&" or "and", or are 3-word names
    where = """
        WHERE bn.confidence IN ('high', 'medium')
        AND (
            bn.person_name LIKE '%%&%%'
            OR bn.person_name LIKE '%% and %%'
            OR (LENGTH(bn.person_name) - LENGTH(REPLACE(bn.person_name, ' ', '')) = 2)
        )
    """
    params = []

    if args.state:
        where += """
            AND bn.bulletin_pdf_id IN (
                SELECT bp.bulletin_pdf_id FROM bulletin_pdf bp
                JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id
                JOIN church c ON c.church_id = bs.church_id
                WHERE c.state_code = %s
            )
        """
        params.append(args.state.upper())

    limit_clause = f"LIMIT {args.limit}" if args.limit else ""

    cur.execute(
        f"""
        SELECT bn.bulletin_name_id, bn.bulletin_pdf_id, bn.person_name,
               bn.role, bn.context, bn.category
        FROM bulletin_name bn
        {where}
        ORDER BY bn.bulletin_name_id
        {limit_clause}
    """,
        params,
    )
    rows = cur.fetchall()

    print(f"  Candidate couple names: {len(rows):,}")

    if not rows:
        return {"candidates": 0, "couples_found": 0, "records_inserted": 0}

    couples_found = 0
    records_inserted = 0

    for row in rows:
        person_name = row["person_name"]
        individuals = detect_couple(person_name)

        # Only process if couple detection found 2+ people
        if len(individuals) < 2:
            continue

        couples_found += 1

        if args.dry_run:
            print(f"    [DRY] '{person_name}' → {[n for n, _ in individuals]}")
            continue

        # Insert each individual as a new record
        for individual_name, split_type in individuals:
            parts = parse_name_parts(individual_name)
            first_name = parts.get("first_name") or ""
            last_name = parts.get("last_name") or ""

            raw_score = score_name_confidence(
                individual_name,
                category=row.get("category") or "",
                role=row.get("role") or "",
                title=parts.get("title") or "",
            )
            conf = confidence_label(raw_score)

            # Check if individual already exists for this PDF
            cur.execute(
                "SELECT 1 FROM bulletin_name "
                "WHERE bulletin_pdf_id = %s AND person_name = %s LIMIT 1",
                (row["bulletin_pdf_id"], individual_name[:100]),
            )
            if cur.fetchone():
                continue

            cur.execute(
                """
                INSERT INTO bulletin_name
                    (bulletin_pdf_id, person_name, first_name, last_name,
                     title, middle_name,
                     confidence, is_suspect, role, context, category)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    row["bulletin_pdf_id"],
                    individual_name[:100],
                    first_name[:50],
                    last_name[:50],
                    (parts.get("title") or "")[:20],
                    (parts.get("middle_name") or "")[:50],
                    conf,
                    0,
                    (row.get("role") or "")[:100],
                    (row.get("context") or "")[:500],
                    (row.get("category") or "")[:30],
                ),
            )
            records_inserted += cur.rowcount

        # Mark original couple record as suspect (keep it but flag it)
        cur.execute(
            "UPDATE bulletin_name SET is_suspect = 1 WHERE bulletin_name_id = %s",
            (row["bulletin_name_id"],),
        )

    print(f"  Couples found: {couples_found:,}, records inserted: {records_inserted:,}")
    return {
        "candidates": len(rows),
        "couples_found": couples_found,
        "records_inserted": records_inserted,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Re-score existing names with NER + couple detection"
    )
    parser.add_argument("--state", type=str, help="One state code (e.g. GA)")
    parser.add_argument("--limit", type=int, default=0, help="Max names to process (0=all)")
    parser.add_argument("--batch-size", type=int, default=500, help="Names per NER batch")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--couples-only", action="store_true", help="Only run couple detection")
    parser.add_argument("--ner-only", action="store_true", help="Only run NER veto")
    parser.add_argument("--skip-stats", action="store_true", help="Skip stats refresh at end")
    args = parser.parse_args()

    state_label = args.state.upper() if args.state else "ALL"
    mode_parts = []
    if args.dry_run:
        mode_parts.append("dry-run")
    if args.couples_only:
        mode_parts.append("couples-only")
    if args.ner_only:
        mode_parts.append("ner-only")
    mode = ", ".join(mode_parts) if mode_parts else "full"

    print("=" * 60)
    print(f"  Re-score with NER + Couple Detection")
    print(f"  State: {state_label}  Mode: {mode}")
    print("=" * 60)

    conn = get_connection()
    cur = conn.cursor()

    start = time.time()
    results = {}

    # Phase A: NER Veto
    if not args.couples_only:
        results["ner"] = run_ner_veto_pass(cur, args)

    # Phase B: Couple Detection
    if not args.ner_only:
        results["couples"] = run_couple_detection(cur, args)

    # Phase C: Refresh stats (unless skipped)
    if not args.skip_stats and not args.dry_run:
        print("\n  Phase C: Refreshing stats...")
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
        print("  [OK] Stats refreshed")

    elapsed = time.time() - start

    # Log the run
    if not args.dry_run:
        ner_info = results.get("ner", {})
        couple_info = results.get("couples", {})
        cur.execute(
            """
            INSERT INTO scrape_log (
                scrape_type, completed_at, status,
                communities_scraped, churches_scraped, services_upserted,
                errors, notes
            ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
        """,
            (
                "ner_rescore",
                "completed",
                None,
                ner_info.get("checked", 0),
                couple_info.get("records_inserted", 0),
                None,
                f"NER: {ner_info.get('checked', 0)} checked, {ner_info.get('downgraded', 0)} downgraded. "
                f"Couples: {couple_info.get('couples_found', 0)} found, {couple_info.get('records_inserted', 0)} inserted. "
                f"Time: {elapsed:.0f}s",
            ),
        )

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESCORE SUMMARY")
    print(f"{'='*60}")
    if "ner" in results:
        print(
            f"  NER: {results['ner']['checked']:,} checked, {results['ner']['downgraded']:,} downgraded"
        )
    if "couples" in results:
        print(
            f"  Couples: {results['couples']['couples_found']:,} found, "
            f"{results['couples']['records_inserted']:,} new records"
        )
    print(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    if args.dry_run:
        print("  (DRY RUN — no changes written)")
    print("[OK] Done!")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
