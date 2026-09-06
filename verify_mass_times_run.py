"""
verify_mass_times_run.py — prove the mass-times scraper is still working.

The mass-times source has died silently before: CatholicIndex went behind a
Cloudflare managed challenge around 2026-04 and the scrape was a no-op for
months while still exiting 0. The current source is DiscoverMass, and it can
go the same way. Exit codes do not tell you; rows do.

Checks, over a window:
  1. churches were re-scraped (church.last_scraped_at moved)
  2. services exist and were refreshed
  3. enough DISTINCT states were touched — the mass-times cron shards states
     across days, so a healthy week spans many states; one or two means the
     rotation is stuck the way bulletins were
  4. nothing has gone globally stale (share of churches not scraped in 30d)

Reports to Telegram pass or fail, because silence looks exactly like a dead cron.

Usage:
    python verify_mass_times_run.py              # last 7 days
    python verify_mass_times_run.py --days 2
    python verify_mass_times_run.py --no-telegram
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.db_connection import get_connection
from src.utils.telegram import send_telegram

MIN_CHURCHES = 500  # a week of sharded runs should refresh thousands
MIN_STATES = 8
MAX_STALE_PCT = 60.0  # share of churches with no scrape in 30 days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    d = args.days

    cur.execute(
        "SELECT COUNT(*) v FROM church WHERE last_scraped_at >= NOW() - INTERVAL %s DAY", (d,)
    )
    scraped = cur.fetchone()["v"]

    cur.execute(
        "SELECT COUNT(DISTINCT state_code) v FROM church "
        "WHERE last_scraped_at >= NOW() - INTERVAL %s DAY",
        (d,),
    )
    states = cur.fetchone()["v"]

    cur.execute("SELECT MAX(last_scraped_at) v FROM church")
    last = cur.fetchone()["v"]

    cur.execute("SELECT COUNT(*) v FROM service")
    services = cur.fetchone()["v"]

    cur.execute(
        "SELECT COUNT(*) total, SUM(last_scraped_at < NOW() - INTERVAL 30 DAY "
        "OR last_scraped_at IS NULL) stale FROM church"
    )
    r = cur.fetchone()
    total, stale = r["total"], int(r["stale"] or 0)
    stale_pct = (stale / total * 100) if total else 100.0

    checks = [
        ("churches re-scraped", scraped, MIN_CHURCHES),
        ("states touched", states, MIN_STATES),
    ]
    failures = [(label, got, floor) for label, got, floor in checks if got < floor]
    if stale_pct > MAX_STALE_PCT:
        failures.append(("stale >30d %", round(stale_pct, 1), MAX_STALE_PCT))

    ok = not failures
    lines = [
        f"{'[OK]' if ok else '[ALERT]'} <b>Mass-times scraper — {'OK' if ok else 'FAIL'}</b>",
        f"window: last {d} day(s)",
        "",
        f"churches re-scraped : {scraped:,}",
        f"states touched      : {states}",
        f"services on file    : {services:,}",
        f"stale &gt;30d          : {stale:,} / {total:,} ({stale_pct:.1f}%)",
        f"last scrape at      : {last}",
    ]
    if failures:
        lines.append("")
        lines.append("below floor:")
        for label, got, floor in failures:
            lines.append(f"  {label}: {got} vs {floor}")

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("&gt;", ">"))

    if not args.no_telegram:
        send_telegram(msg)

    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
