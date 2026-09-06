"""
run_discovermass_all.py — Refresh mass times from DiscoverMass across states.

Runs `discovermass_to_db99.py --state <X> --commit` for each state, continuing on
failure so one bad state never blocks the rest. Cherry Road states are processed
FIRST (the markets we care most about), then the remaining states.

Each per-state run is idempotent (match-or-insert + per-state stale cleanup), so
re-running after a partial/throttled run self-heals.

Set PROXY_URL in the env to route through the residential proxy (dodges
DiscoverMass's Crawl-delay:10 throttle). db99 creds come from AWS Secrets Manager
on Render (or DB_HOST=10.10.0.8 locally).

Usage:
    python run_discovermass_all.py                 # CR states first, then all 50
    python run_discovermass_all.py --states KS,MO  # only these
    python run_discovermass_all.py --cr-only       # only Cherry Road states
"""

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The mass-times cron fires 6 days/week: `0 3 * * 0,1,3,4,5,6` (every day except
# Tuesday). In Python's weekday() (Mon=0 .. Sun=6) that is these six days, in
# chronological week order. --shard-dow assigns each state to one of these days so
# the whole country is refreshed once per week and each fire finishes in a cron
# window (instead of restarting a ~30h national re-scrape that never completes).
ACTIVE_WEEKDAYS = [0, 2, 3, 4, 5, 6]  # Mon, Wed, Thu, Fri, Sat, Sun

# Cherry Road market states (from cr_market_shape) — refreshed first.
CR_STATES = [
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
]
ALL_STATES = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
]


def ordered_states(cr_only: bool):
    if cr_only:
        return list(CR_STATES)
    rest = [s for s in ALL_STATES if s not in CR_STATES]
    return CR_STATES + rest


def shard_for_today(weekday: int) -> list[str]:
    """Return the slice of states assigned to today's cron fire.

    States are ordered Cherry-Road-first, then round-robined into 6 buckets (one
    per active cron day) so CR states spread evenly across the week. Bucket i gets
    states[i::6]. Today's bucket index is this weekday's position among the active
    days; a run on the one non-active day (Tue) falls back to weekday % 6 so a
    manual invocation still does useful work.
    """
    states = ordered_states(cr_only=False)
    n_buckets = len(ACTIVE_WEEKDAYS)
    idx = ACTIVE_WEEKDAYS.index(weekday) if weekday in ACTIVE_WEEKDAYS else weekday % n_buckets
    return states[idx::n_buckets]


def run_state(state: str, timeout_s: int, stale_days: int = 0) -> bool:
    cmd = [
        sys.executable,
        "-u",
        str(ROOT / "discovermass_to_db99.py"),
        "--state",
        state,
        "--commit",
    ]
    if stale_days > 0:
        cmd += ["--stale-days", str(stale_days)]
    print(f"\n{'='*60}\n[RUN] DiscoverMass commit: {state}\n{'='*60}", flush=True)
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), timeout=timeout_s)
        ok = r.returncode == 0
        print(f"[{'OK' if ok else 'ERR'}] {state} exit={r.returncode}", flush=True)
        return ok
    except subprocess.TimeoutExpired:
        print(f"[ERR] {state} timed out after {timeout_s}s", flush=True)
        return False


def main():
    ap = argparse.ArgumentParser(description="Refresh DiscoverMass mass times by state")
    ap.add_argument("--states", type=str, help="Comma-separated subset (e.g. KS,MO)")
    ap.add_argument("--cr-only", action="store_true", help="Only Cherry Road states")
    ap.add_argument(
        "--shard-dow",
        action="store_true",
        help="Run only today's day-of-week shard (~1/6 of states) so the whole "
        "country is refreshed weekly and each cron fire finishes in its window.",
    )
    ap.add_argument(
        "--stale-days",
        type=int,
        default=0,
        help="Skip churches scraped within the last N days (passed to "
        "discovermass_to_db99.py). 0=disabled.",
    )
    ap.add_argument(
        "--per-state-timeout", type=int, default=21600, help="Seconds per state (default 6h)"
    )
    args = ap.parse_args()

    if args.states:
        states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    elif args.shard_dow:
        weekday = datetime.now(UTC).weekday()
        states = shard_for_today(weekday)
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]
        print(f"[SHARD] weekday={day_name} -> {len(states)} states this fire", flush=True)
    else:
        states = ordered_states(args.cr_only)

    print(f"[START] DiscoverMass refresh for {len(states)} states: {', '.join(states)}", flush=True)
    if args.stale_days > 0:
        print(
            f"[START] stale-only: skipping churches scraped within {args.stale_days}d", flush=True
        )
    results = {}
    for st in states:
        results[st] = run_state(st, args.per_state_timeout, stale_days=args.stale_days)
        time.sleep(2)

    ok = [s for s, v in results.items() if v]
    bad = [s for s, v in results.items() if not v]
    print(f"\n[DONE] OK={len(ok)} FAIL={len(bad)}", flush=True)
    if bad:
        print(f"[DONE] failed/partial states (safe to re-run): {', '.join(bad)}", flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
