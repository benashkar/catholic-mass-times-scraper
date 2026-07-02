"""Emit a line each time a new Cherry Road state's churches get refreshed from
DiscoverMass in db99. Exits when all 21 CR states are in. For use with Monitor.

Run: DB_HOST=10.10.0.8 python -u scripts/_cr_progress_monitor.py
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DB_HOST", "10.10.0.8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils.db_connection import get_connection  # noqa: E402

CR = {
    "KS",
    "MO",
    "MN",
    "OH",
    "AR",
    "TX",
    "OK",
    "UT",
    "CO",
    "IN",
    "IA",
    "NE",
    "NY",
    "GA",
    "AL",
    "MI",
    "ID",
    "MA",
    "IL",
    "NM",
    "ME",
}
SINCE = "2026-06-18"

seen = set()
for _ in range(300):  # ~15h at 180s
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
        conn.close()
    except Exception as e:
        print(f"[poll-err] {str(e)[:80]}", flush=True)
        time.sleep(180)
        continue
    cur_cr = set(counts) & CR
    for s in sorted(cur_cr - seen):
        print(f"[CR landed] {s}: {counts[s]} churches  ({len(cur_cr)}/21 CR states)", flush=True)
    seen |= cur_cr
    if seen >= CR:
        print("[CR DONE] all 21 Cherry Road states refreshed from DiscoverMass", flush=True)
        break
    time.sleep(180)
