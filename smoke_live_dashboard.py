"""smoke_live_dashboard.py — hit the DEPLOYED dashboard and fail on empty screens.

The in-process canaries in tests/test_dashboard_canaries.py cannot see the
failure that actually broke /bulletin/ on 2026-08-14: each gunicorn worker
holds its own copy of the state cache, so a worker whose boot-time load failed
served an empty page while its siblings served real data. One request proves
nothing — a single check had a ~50% chance of hitting a healthy worker and
reporting all clear.

So this asks for each screen REPEATEDLY and fails if ANY response comes back
empty. Enough repeats to make missing a bad worker unlikely.

Usage:
    python smoke_live_dashboard.py                     # uses DASHBOARD_URL
    python smoke_live_dashboard.py --repeats 12
    python smoke_live_dashboard.py --no-telegram
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.environ.get("DASHBOARD_URL", "https://catholic-church-dashboard-va.onrender.com")
EMPTY_MARKER = "No bulletin name data available yet"

# path -> (minimum plausible byte size, what disappears when it comes back short)
SCREENS = {
    "/": (20000, "home state cards"),
    "/bulletin/": (20000, "bulletin state grid"),
    "/bulletin/wisconsin/": (20000, "Wisconsin names table"),
    "/mass-times/ohio/": (100000, "Ohio church list"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--repeats",
        type=int,
        default=10,
        help="Requests per screen. Each gunicorn worker must be sampled.",
    )
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    users = os.environ.get("DASHBOARD_USERS", "")
    if not users or ":" not in users:
        print("[ERR] DASHBOARD_USERS not set; cannot log in")
        return 2
    username, password = users.split(",")[0].split(":", 1)

    s = requests.Session()
    r = s.post(
        f"{BASE}/login",
        data={"username": username, "password": password},
        allow_redirects=False,
        timeout=90,
    )
    if r.status_code != 302:
        print(f"[ERR] login failed: {r.status_code}")
        return 2

    failures = []
    for path, (min_bytes, element) in SCREENS.items():
        sizes, bad = [], 0
        for _ in range(args.repeats):
            try:
                resp = s.get(f"{BASE}{path}", timeout=240)
            except Exception as e:
                bad += 1
                failures.append(f"{path}: request failed ({type(e).__name__}: {e})")
                continue
            sizes.append(len(resp.text))
            if resp.status_code != 200:
                bad += 1
                failures.append(f"{path}: HTTP {resp.status_code} — {element} vanishes")
            elif EMPTY_MARKER in resp.text:
                bad += 1
                failures.append(f"{path}: empty-state banner — {element} vanishes")
            elif len(resp.text) < min_bytes:
                bad += 1
                failures.append(
                    f"{path}: {len(resp.text):,} bytes < {min_bytes:,} — {element} looks empty"
                )
        rng = f"{min(sizes):,}-{max(sizes):,}" if sizes else "n/a"
        print(
            f"  {'[OK] ' if not bad else '[FAIL]'} {path:<26} "
            f"{args.repeats - bad}/{args.repeats} good, bytes {rng}"
        )

    # Downloads must not silently shrink.
    r = s.get(f"{BASE}/bulletin/wisconsin/api/names.csv?confidence=high", timeout=600)
    total = r.headers.get("X-Total-Matching-Rows")
    returned = r.headers.get("X-Rows-Returned")
    truncated = r.headers.get("X-Truncated")
    print(
        f"  {'[FAIL]' if truncated else '[OK] '} CSV rows {returned}/{total}"
        f"{' TRUNCATED' if truncated else ''}"
    )
    if truncated:
        failures.append(f"CSV truncated: {returned} of {total} rows returned")

    # Dedupe so one flapping worker does not print the same line ten times.
    unique = sorted(set(failures))
    if unique:
        print(f"\n{len(failures)} failed check(s), {len(unique)} distinct:")
        for f in unique:
            print(f"  - {f}")
    else:
        print("\nALL SCREENS OK")

    if not args.no_telegram and unique:
        try:
            from src.utils.telegram import send_telegram

            send_telegram("<b>Dashboard smoke FAILED</b>\n" + "\n".join(unique[:10]))
        except Exception as e:
            print(f"  [WARN] telegram: {e}")

    return 1 if unique else 0


if __name__ == "__main__":
    sys.exit(main())
