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
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Cherry Road market states (from cr_market_shape) — refreshed first.
CR_STATES = [
    "KS", "MO", "MN", "OH", "AR", "TX", "OK", "UT", "CO", "IN", "IA",
    "NE", "NY", "GA", "AL", "MI", "ID", "MA", "IL", "NM", "ME",
]
ALL_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]


def ordered_states(cr_only: bool):
    if cr_only:
        return list(CR_STATES)
    rest = [s for s in ALL_STATES if s not in CR_STATES]
    return CR_STATES + rest


def run_state(state: str, timeout_s: int) -> bool:
    cmd = [sys.executable, "-u", str(ROOT / "discovermass_to_db99.py"), "--state", state, "--commit"]
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
    ap.add_argument("--per-state-timeout", type=int, default=21600, help="Seconds per state (default 6h)")
    args = ap.parse_args()

    if args.states:
        states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    else:
        states = ordered_states(args.cr_only)

    print(f"[START] DiscoverMass refresh for {len(states)} states: {', '.join(states)}", flush=True)
    results = {}
    for st in states:
        results[st] = run_state(st, args.per_state_timeout)
        time.sleep(2)

    ok = [s for s, v in results.items() if v]
    bad = [s for s, v in results.items() if not v]
    print(f"\n[DONE] OK={len(ok)} FAIL={len(bad)}", flush=True)
    if bad:
        print(f"[DONE] failed/partial states (safe to re-run): {', '.join(bad)}", flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
