"""
backfill_pdf_dates.py — populate bulletin_pdf.pdf_date from the PDF URL.

The direct-to-db99 extractor never parsed the edition date out of the URL, so
rows it inserted have pdf_date NULL. Without it there is no way to tell which
editions we hold and which we missed. The date lives in the filename for most
parish CMSes (.../bulletins/20230521_t-1684641512000.pdf).

Undated URLs stay NULL — the download timestamp is NOT the publish date.

Usage:
    python backfill_pdf_dates.py --dry-run          # report only, no writes
    python backfill_pdf_dates.py --commit           # write
    python backfill_pdf_dates.py --commit --limit 50000
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extract_bulletins_to_db99 import pdf_date_from_url
from src.utils.db_connection import get_connection

BATCH = 5000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="Write changes (default is dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="Report only")
    ap.add_argument("--limit", type=int, default=0, help="Max rows to scan (0=all)")
    args = ap.parse_args()
    write = args.commit and not args.dry_run

    conn = get_connection(autocommit=False)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) v FROM bulletin_pdf WHERE pdf_date IS NULL")
    todo = cur.fetchone()["v"]
    print(f"rows missing pdf_date: {todo:,}")
    print(f"mode: {'COMMIT' if write else 'DRY-RUN'}")

    scanned = parsed = written = 0
    last_id = 0
    while True:
        cur.execute(
            "SELECT bulletin_pdf_id, pdf_url FROM bulletin_pdf "
            "WHERE pdf_date IS NULL AND bulletin_pdf_id > %s "
            "ORDER BY bulletin_pdf_id LIMIT %s",
            (last_id, BATCH),
        )
        rows = cur.fetchall()
        if not rows:
            break

        updates = []
        for r in rows:
            last_id = r["bulletin_pdf_id"]
            scanned += 1
            d = pdf_date_from_url(r["pdf_url"])
            if d:
                parsed += 1
                updates.append((d, r["bulletin_pdf_id"]))

        if updates and write:
            # One statement per batch, not one per row. executemany() here means
            # 5,000 round trips, which took ~4 minutes per batch (~14h overall);
            # a single CASE update collapses that to one.
            case = " ".join(f"WHEN {pid} THEN %s" for _, pid in updates)
            ids = ",".join(str(pid) for _, pid in updates)
            cur.execute(
                f"UPDATE bulletin_pdf SET pdf_date = CASE bulletin_pdf_id {case} END "
                f"WHERE bulletin_pdf_id IN ({ids})",
                [d for d, _ in updates],
            )
            conn.commit()
            written += len(updates)

        pct = (parsed / scanned * 100) if scanned else 0
        print(f"  scanned={scanned:,} parsed={parsed:,} ({pct:.1f}%) written={written:,}", flush=True)

        if args.limit and scanned >= args.limit:
            break

    print(f"\nscanned={scanned:,} parsed={parsed:,} written={written:,}")
    if not write:
        print("DRY-RUN — nothing written. Re-run with --commit.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
