"""National bulletin coverage, per state, reported back through the mailbox.

verify_wi_bulletins.py answers one state. After a national sweep the question
is which states moved and which are still dark, and a single roll-up hides
that — a healthy national total can sit on top of a dozen states at zero, which
is exactly how the bulletin cron covered only AK/AL/AR/AZ/CA for five months
while reporting "completed" every week.

So: per-state rows, sorted by the number of churches still producing nothing,
because that is the work queue.

Read-only. Writes nothing.

Usage (Render one-off):
    python -u verify_bulletins_national.py
    python -u verify_bulletins_national.py --top 60
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=60, help="How many states to list")
    args = ap.parse_args()

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT c.state_code AS st,
               COUNT(*) AS churches,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM bulletin_source bs
                                     WHERE bs.church_id = c.church_id)
                        THEN 1 ELSE 0 END) AS with_source,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM bulletin_source bs
                                     JOIN bulletin_pdf bp
                                       ON bp.bulletin_source_id = bs.bulletin_source_id
                                     WHERE bs.church_id = c.church_id)
                        THEN 1 ELSE 0 END) AS with_pdf
        FROM church c
        WHERE c.website_url IS NOT NULL AND c.website_url <> ''
        GROUP BY c.state_code
        ORDER BY (with_source - with_pdf) DESC
        """
    )
    rows = cur.fetchall()

    tot_ch = sum(r["churches"] for r in rows)
    tot_src = sum(int(r["with_source"] or 0) for r in rows)
    tot_pdf = sum(int(r["with_pdf"] or 0) for r in rows)

    lines = [
        "national bulletin coverage",
        "",
        f"states {len(rows)}   churches {tot_ch:,}   with_source {tot_src:,}   "
        f"with_pdf {tot_pdf:,}",
        f"gap (source but no PDF): {tot_src - tot_pdf:,}",
        "",
        f"{'st':4} {'churches':>9} {'source':>8} {'pdf':>8} {'gap':>8}  {'%pdf':>6}",
        "-" * 52,
    ]
    for r in rows[: args.top]:
        src = int(r["with_source"] or 0)
        pdf = int(r["with_pdf"] or 0)
        pct = (100.0 * pdf / src) if src else 0.0
        lines.append(
            f"{r['st'] or '??':4} {r['churches']:9,} {src:8,} {pdf:8,} "
            f"{src - pdf:8,}  {pct:5.1f}%"
        )

    # Freshness: a sweep that runs but collects nothing current is the failure
    # mode that hid for five months, so ask when the newest edition landed.
    cur.execute(
        """
        SELECT MAX(bp.pdf_date) AS newest, COUNT(*) AS n
        FROM bulletin_pdf bp
        WHERE bp.pdf_date IS NOT NULL
        """
    )
    r = cur.fetchone()
    lines.append("")
    lines.append(f"newest edition anywhere: {r['newest']}   dated PDFs: {r['n']:,}")

    cur.execute(
        """
        SELECT bp.pdf_date, COUNT(DISTINCT bs.church_id) AS parishes
        FROM bulletin_pdf bp
        JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id
        WHERE bp.pdf_date IS NOT NULL
        GROUP BY bp.pdf_date ORDER BY bp.pdf_date DESC LIMIT 6
        """
    )
    lines.append("")
    lines.append("--- parishes per recent edition ---")
    for x in cur.fetchall():
        lines.append(f"  {x['pdf_date']}  {x['parishes']:,}")

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)
    publish("verify_bulletins_national", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "verify_bulletins_national FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("verify_bulletins_national", tb)
        sys.exit(1)
