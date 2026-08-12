"""
verify_bulletin_run.py — weekly proof that the bulletin PDF scraper actually ran.

The bulletin cron logged bulletins=FAIL every Tuesday from 2026-07-14 to
2026-08-04 and nobody noticed, because the step is non-critical so the pipeline
still reported "completed" and names still trickled in from the handful of
states the stuck loop could reach. Green-looking output is not proof of work.

So this asserts on the DATA, not on exit codes:

  1. PDFs were downloaded in the window
  2. names were inserted in the window
  3. UNIQUE names were inserted (a rerun over the same bulletins re-inserts
     nothing new, so unique-vs-total is what tells you it really moved)
  4. more than a handful of distinct parishes were touched — the signature of
     the old bug was ~430 parishes stuck in the first alphabetical states
  5. enough churches were actually CHECKED — a queue that drains early because
     the freshness window is longer than the cron period looks identical to a
     healthy run from the outside (14 min, exit 0, "completed"), and that is
     what happened on 2026-08-11
  6. editions were dated (pdf_date), so coverage stays measurable

Prefer --since-minutes over --days when checking a specific run: a trailing
7-day window lets a manual sweep from earlier in the week vouch for a cron run
that did nothing.

Sends the result to Telegram either way: silence is indistinguishable from a
dead cron, so a healthy run reports too.

Usage:
    python verify_bulletin_run.py                    # last 7 days
    python verify_bulletin_run.py --since-minutes 90 # just this run
    python verify_bulletin_run.py --days 1
    python verify_bulletin_run.py --no-telegram
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.db_connection import get_connection
from src.utils.telegram import send_telegram

# A healthy weekly sweep clears these comfortably; they are floors for
# "something ran", not targets.
MIN_PDFS = 50
MIN_NAMES = 500
MIN_UNIQUE_NAMES = 100
MIN_PARISHES = 40
# A weekly sweep at --days-fresh 6 has ~17k churches due. 1,000 is a floor for
# "the queue was not empty", set well under a healthy run but well over the 378
# a drained queue produced on 2026-08-11.
MIN_CHURCHES_CHECKED = 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="Window to check, in days")
    ap.add_argument(
        "--since-minutes",
        type=int,
        default=0,
        help="Window in MINUTES instead of days — use this to check one run",
    )
    ap.add_argument("--no-telegram", action="store_true", help="Print only")
    args = ap.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    # One window expression, applied identically to every check. Both units go
    # through the DB clock so a container/db clock skew cannot widen or shrink
    # the window under us.
    if args.since_minutes:
        unit, d = "MINUTE", args.since_minutes
        window_label = f"last {d} minute(s) — this run only"
    else:
        unit, d = "DAY", args.days
        window_label = f"last {d} day(s)"

    cur.execute(
        f"SELECT COUNT(*) v FROM bulletin_pdf WHERE downloaded_at >= NOW() - INTERVAL %s {unit}",
        (d,),
    )
    pdfs = cur.fetchone()["v"]

    cur.execute(
        f"SELECT COUNT(*) total, COUNT(DISTINCT person_name) uniq "
        f"FROM bulletin_name WHERE created_at >= NOW() - INTERVAL %s {unit}",
        (d,),
    )
    r = cur.fetchone()
    names, uniq = r["total"], r["uniq"]

    cur.execute(
        f"SELECT COUNT(DISTINCT bp.bulletin_source_id) v FROM bulletin_pdf bp "
        f"WHERE bp.downloaded_at >= NOW() - INTERVAL %s {unit}",
        (d,),
    )
    parishes = cur.fetchone()["v"]

    # Churches CLAIMED in the window. extract_bulletins stamps this before it
    # processes each church, hit or miss, so it measures how much of the queue
    # the run actually got through — the one number a drained queue cannot fake.
    cur.execute(
        f"SELECT COUNT(*) v FROM church WHERE bulletin_checked_at >= NOW() - INTERVAL %s {unit}",
        (d,),
    )
    checked = cur.fetchone()["v"]

    cur.execute(
        f"SELECT COUNT(*) v FROM bulletin_pdf "
        f"WHERE downloaded_at >= NOW() - INTERVAL %s {unit} AND pdf_date IS NOT NULL",
        (d,),
    )
    dated = cur.fetchone()["v"]

    cur.execute(
        f"SELECT COUNT(DISTINCT c.state_code) v FROM bulletin_pdf bp "
        f"JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id "
        f"JOIN church c ON c.church_id = bs.church_id "
        f"WHERE bp.downloaded_at >= NOW() - INTERVAL %s {unit}",
        (d,),
    )
    states = cur.fetchone()["v"]

    cur.execute("SELECT MAX(downloaded_at) v FROM bulletin_pdf")
    last_pdf = cur.fetchone()["v"]

    checks = [
        ("PDFs downloaded", pdfs, MIN_PDFS),
        ("names inserted", names, MIN_NAMES),
        ("UNIQUE names", uniq, MIN_UNIQUE_NAMES),
        ("parishes touched", parishes, MIN_PARISHES),
        ("churches checked", checked, MIN_CHURCHES_CHECKED),
    ]
    failures = [(label, got, floor) for label, got, floor in checks if got < floor]
    ok = not failures

    status = "OK" if ok else "FAIL"
    lines = [
        f"{'[OK]' if ok else '[ALERT]'} <b>Bulletin PDF scraper — {status}</b>",
        f"window: {window_label}",
        "",
        f"churches checked: {checked:,}",
        f"PDFs downloaded : {pdfs:,}",
        f"names inserted  : {names:,}",
        f"UNIQUE names    : {uniq:,}",
        f"parishes        : {parishes:,}",
        f"states          : {states}",
        f"editions dated  : {dated:,}",
        f"last PDF at     : {last_pdf}",
    ]
    if failures:
        lines.append("")
        lines.append("below floor:")
        for label, got, floor in failures:
            lines.append(f"  {label}: {got:,} &lt; {floor:,}")
    # The old bug's fingerprint: work happening, but only in a few states.
    if states and states < 10 and pdfs > 0:
        lines.append("")
        lines.append(f"WARNING: only {states} states touched — possible stuck rotation")

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("&lt;", "<"))

    if not args.no_telegram:
        send_telegram(msg)

    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
