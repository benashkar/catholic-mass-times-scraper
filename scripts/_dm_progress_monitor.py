"""Emit a line each time a NEW state's churches get refreshed from DiscoverMass
today, with the running total. For tracking a manual run_discovermass_all run.

Run: DB_HOST=10.10.0.8 python -u scripts/_dm_progress_monitor.py 2026-06-22
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DB_HOST", "10.10.0.8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils.db_connection import close_quietly, get_connection  # noqa: E402

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-06-22"
seen = set()
last_total = 0
for _ in range(360):  # ~18h at 180s
    # close() in a finally, not on the happy path: the except below sleeps and
    # continues, so a failing poll used to leak a connection every 180s for up
    # to 18 hours -- while db99 was already erroring. See _cr_progress_monitor.
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT state_code, COUNT(*) n FROM church "
            "WHERE source_url LIKE '%%discovermass%%' AND last_scraped_at >= %s "
            "GROUP BY state_code",
            (SINCE,),
        )
        counts = {r["state_code"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) n FROM church WHERE source_url LIKE '%%discovermass%%'")
        total = cur.fetchone()["n"]
    except Exception as e:
        print(f"[poll-err] {str(e)[:80]}", flush=True)
        time.sleep(180)
        continue
    finally:
        close_quietly(conn)
    for s in sorted(set(counts) - seen):
        print(
            f"[new state] {s}: {counts[s]} churches today  |  total DiscoverMass churches now {total}",
            flush=True,
        )
    seen |= set(counts)
    last_total = total
    time.sleep(180)
print(
    f"[monitor-end] total DiscoverMass churches {last_total}, states refreshed today {len(seen)}",
    flush=True,
)
