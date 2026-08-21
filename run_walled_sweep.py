"""Scheduled walled-host recovery, spread across the week by state.

WHY THIS EXISTS
---------------
The walled-host pass — a headless browser THROUGH the residential proxy for
hosts labelled `blocked` — recovered **+5,532 churches nationally** (7,584 ->
13,116) over 2026-08-18..21. Every one of those runs was launched and
babysat BY HAND. Nothing scheduled ever ran it: `--walled-browser` appeared in
no cron, no pipeline, and no supervisor. So the moment the operator stopped
watching, churches needing browser+proxy would silently return to yielding zero
— indistinguishable, from the outside, from "these parishes have no bulletins".
That is the exact failure this whole effort existed to fix.

It cannot simply be switched on inside the main sweep: a Chromium per blocked
host alongside spaCy and pdfplumber does not fit the 2GB cron plan, and doing
that OOM-killed three shards on its first outing. So it gets its own cron, its
own container, one worker, and a per-run browser budget.

HOW THE WEEK IS DIVIDED
-----------------------
States are ordered by CURRENT GAP (churches with a bulletin source but no PDF),
biggest first, then round-robined into 7 buckets — `states[i::7]`. Round-robin
rather than contiguous slices so each day gets a mix of large and small states
instead of one day drawing all four of NY/CA/TX/PA. Ordering by gap means the
states with the most to win are always in the rotation, and it re-orders itself
as gaps close — no hand-maintained list to go stale.

Every state is therefore retried weekly, unattended.

Usage:
    python run_walled_sweep.py                 # today's bucket
    python run_walled_sweep.py --dry-run       # print the plan, run nothing
    python run_walled_sweep.py --states WI,MN  # explicit override
    python run_walled_sweep.py --day 3         # pretend it is a given weekday
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BUCKETS = 7
# One worker: this pass launches a browser per walled host and the plan is 2GB.
WORKERS = int(os.environ.get("WALLED_WORKERS", "1"))
# Per-run ceiling on browser launches, so one pathological state cannot spawn
# Chromium without limit. Sized for a day's bucket, not a single state.
BUDGET = int(os.environ.get("WALLED_BUDGET", "1200"))
# Leave headroom inside the plan's runtime cap; a state that overruns is picked
# up again next week rather than blocking the rest of the bucket.
PER_STATE_TIMEOUT_S = int(os.environ.get("WALLED_STATE_TIMEOUT_S", "5400"))


def states_by_gap():
    """States ordered by current bulletin gap, largest first.

    Reading the gap live means the rotation follows the work instead of a
    hardcoded list that rots as coverage changes.
    """
    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.state_code AS st,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM bulletin_source bs
                                     WHERE bs.church_id = c.church_id)
                    AND NOT EXISTS (SELECT 1 FROM bulletin_source bs
                                    JOIN bulletin_pdf bp
                                      ON bp.bulletin_source_id = bs.bulletin_source_id
                                    WHERE bs.church_id = c.church_id)
                   THEN 1 ELSE 0 END) AS gap
        FROM church c
        WHERE c.website_url IS NOT NULL AND c.website_url <> ''
        GROUP BY c.state_code
        HAVING gap > 0
        ORDER BY gap DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(r["st"], int(r["gap"])) for r in rows if r["st"]]


def bucket_for_day(ordered, weekday):
    """Round-robin, so each day gets a mix of large and small states."""
    return ordered[weekday % BUCKETS :: BUCKETS]


def run_state(state, budget):
    cmd = [
        sys.executable, "-u", "extract_bulletins_to_db99.py",
        "--state", state,
        "--walled-browser",
        "--walled-budget", str(budget),
        "--workers", str(WORKERS),
        "--days-fresh", "0",
    ]
    print(f"  -> {' '.join(cmd)}", flush=True)
    try:
        r = subprocess.run(cmd, timeout=PER_STATE_TIMEOUT_S)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        # Not a failure worth alarming on: the per-church watermark means the
        # next run resumes at the frontier rather than redoing work.
        print(f"  [--] {state} hit the per-state timeout; resumes next cycle", flush=True)
        return False
    except Exception as e:
        print(f"  [ERR] {state}: {type(e).__name__}: {e}", flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--states", default="", help="Comma-separated override")
    ap.add_argument("--day", type=int, default=-1, help="Pretend weekday (0=Mon)")
    ap.add_argument("--budget", type=int, default=0)
    args = ap.parse_args()

    weekday = args.day if args.day >= 0 else datetime.now(UTC).weekday()

    if args.states:
        todays = [(s.strip().upper(), 0) for s in args.states.split(",") if s.strip()]
    else:
        ordered = states_by_gap()
        todays = bucket_for_day(ordered, weekday)
        print(f"{len(ordered)} states have a gap; bucket {weekday % BUCKETS} of {BUCKETS}",
              flush=True)

    budget = args.budget or BUDGET
    print(f"walled sweep — weekday={weekday} states={[s for s, _ in todays]} "
          f"workers={WORKERS} budget={budget}", flush=True)
    for st, gap in todays:
        print(f"  {st}: gap={gap}", flush=True)

    if args.dry_run:
        print("DRY RUN — nothing executed.", flush=True)
        return 0

    ok = 0
    for st, _ in todays:
        print(f"\n=== {st} ===", flush=True)
        if run_state(st, budget):
            ok += 1
        time.sleep(3)

    summary = f"walled sweep done: {ok}/{len(todays)} states completed cleanly"
    print("
" + summary, flush=True)

    # Report, or this cron is invisible. A Render one-off's stdout is not
    # retrievable (GET /v1/logs returns logs: null once it exits), so a job that
    # only prints has no readout — and a walled sweep that quietly stopped
    # working would look exactly like "these parishes have no bulletins", which
    # is the failure this cron exists to prevent.
    try:
        from src.utils.telegram import send_telegram

        states_ran = ", ".join(st for st, _ in todays) or "(none)"
        send_telegram(
            f"<b>walled sweep</b> weekday={weekday}
"
            f"states: {states_ran}
"
            f"{ok}/{len(todays)} completed cleanly"
        )
    except Exception as exc:  # reporting must never fail the run
        print(f"  [WARN] telegram: {exc}", flush=True)
    # Exit 0 even on per-state failures: the watermark makes them resumable, and
    # a red cron for one OOM'd state would train everyone to ignore the alarm.
    return 0


if __name__ == "__main__":
    sys.exit(main())
