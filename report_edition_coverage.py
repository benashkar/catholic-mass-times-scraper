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
from src.utils.telegram import send_telegram

# The bulletin cron made no forward progress from this date until 2026-08-05:
# coverage fell from 1,459 parishes/week to 433 in a single week and decayed
# from there. Recovery is judged by whether these weeks refilled.
GAP_START = "2026-02-23"
GAP_END = "2026-08-05"


def gap_summary(cur):
    """Compact before/during-gap comparison, small enough for a Telegram message."""
    cur.execute(
        """
        SELECT DATE_FORMAT(pdf_date, '%%Y-%%m') m,
               COUNT(*) editions,
               COUNT(DISTINCT bulletin_source_id) parishes
        FROM bulletin_pdf
        WHERE pdf_date IS NOT NULL
          AND pdf_date >= DATE_SUB(%s, INTERVAL 3 MONTH)
          AND pdf_date <= %s
        GROUP BY 1 ORDER BY 1
        """,
        (GAP_START, GAP_END),
    )
    rows = cur.fetchall()
    lines = ["<b>Bulletin edition coverage — gap recovery</b>", "month  parishes  editions"]
    for r in rows:
        marker = "  <- gap" if r["m"] >= GAP_START[:7] else ""
        lines.append(f"{r['m']}  {r['parishes']:>6,}  {r['editions']:>7,}{marker}")

    pre = [r["parishes"] for r in rows if r["m"] < GAP_START[:7]]
    during = [r["parishes"] for r in rows if r["m"] >= GAP_START[:7]]
    if pre and during:
        base = max(pre)
        worst = min(during)
        lines.append("")
        lines.append(f"pre-gap best month : {base:,} parishes")
        lines.append(f"worst gap month    : {worst:,} ({worst/base*100:.0f}% of pre-gap)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=52, help="How many weeks back to show")
    ap.add_argument("--state", type=str, help="Limit to one state")
    ap.add_argument("--telegram", action="store_true", help="Send the gap summary to Telegram")
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

    if args.telegram:
        summary = gap_summary(cur)
        print("\n" + summary.replace("<b>", "").replace("</b>", ""))
        send_telegram(summary)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
