"""
report_edition_coverage.py — which bulletin editions do we actually hold?

Bulletins are weekly, so coverage is measured in ISO weeks: for each week,
how many distinct parishes do we have an edition for. A dip marks weeks the
pipeline missed; the dip refilling marks a recovered archive.

Counts editions by pdf_date (when the bulletin was PUBLISHED), never by
downloaded_at (when we happened to fetch it) — a backfill pulls old editions
today, and conflating the two would hide the very gap this reports on.

Usage:
    python report_edition_coverage.py                 # last 52 weeks
    python report_edition_coverage.py --weeks 104
    python report_edition_coverage.py --state OH
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.db_connection import get_connection


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=52, help="How many weeks back to show")
    ap.add_argument("--state", type=str, help="Limit to one state")
    args = ap.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    where = ["bp.pdf_date IS NOT NULL", "bp.pdf_date >= CURDATE() - INTERVAL %s WEEK"]
    params = [args.weeks]
    join = ""
    if args.state:
        join = (
            "JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id "
            "JOIN church c ON c.church_id = bs.church_id"
        )
        where.append("c.state_code = %s")
        params.append(args.state.upper())

    cur.execute(
        f"""
        SELECT YEARWEEK(bp.pdf_date, 3) yw,
               MIN(bp.pdf_date) week_start,
               COUNT(*) editions,
               COUNT(DISTINCT bp.bulletin_source_id) parishes
        FROM bulletin_pdf bp {join}
        WHERE {' AND '.join(where)}
        GROUP BY yw ORDER BY yw
        """,
        params,
    )
    rows = cur.fetchall()
    if not rows:
        print("No dated editions in range.")
        return 0

    peak = max(r["parishes"] for r in rows)
    print(f"{'week of':<12} {'parishes':>9} {'editions':>9}   coverage vs best week")
    print("-" * 74)
    for r in rows:
        bar = "#" * int(r["parishes"] / peak * 34) if peak else ""
        pct = r["parishes"] / peak * 100 if peak else 0
        print(f"{str(r['week_start']):<12} {r['parishes']:>9,} {r['editions']:>9,}   {bar:<34} {pct:5.1f}%")

    print()
    print(f"best week: {peak:,} parishes")
    weak = [r for r in rows if r["parishes"] < peak * 0.5]
    if weak:
        print(f"weeks below 50% of best ({len(weak)}): "
              f"{', '.join(str(r['week_start']) for r in weak[:14])}"
              f"{' ...' if len(weak) > 14 else ''}")

    cur.execute("SELECT COUNT(*) t, COUNT(pdf_date) d FROM bulletin_pdf")
    r = cur.fetchone()
    print(f"\npdf_date populated: {r['d']:,} / {r['t']:,} ({r['d']/r['t']*100:.1f}%)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
