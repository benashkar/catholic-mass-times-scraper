"""
verify_all.py — run every scraper verification, report all, fail if any failed.

A wrapper rather than a shell one-liner because quoting `sh -c "...; b=$?; ..."`
through the Render API is fragile and fails opaquely. Running both checks in
one process also guarantees the second still runs when the first fails, which
is exactly when you want to see both.

Exit code is the worst of the children, so the cron goes red if either scraper
has stopped doing real work.

Usage:
    python verify_all.py            # last 7 days
    python verify_all.py --days 2
"""

import argparse
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

CHECKS = [
    ("bulletin PDFs", "verify_bulletin_run.py"),
    ("mass times", "verify_mass_times_run.py"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    worst = 0
    results = []
    for label, script in CHECKS:
        cmd = [sys.executable, script, "--days", str(args.days)]
        if args.no_telegram:
            cmd.append("--no-telegram")
        print(f"\n{'='*60}\n  {label}\n{'='*60}", flush=True)
        try:
            rc = subprocess.run(cmd, cwd=PROJECT_ROOT, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print(f"  [ERR] {label} timed out")
            rc = 1
        except Exception as e:
            print(f"  [ERR] {label}: {e}")
            rc = 1
        results.append((label, rc))
        worst = max(worst, rc)

    print(f"\n{'='*60}")
    for label, rc in results:
        print(f"  {'[OK]' if rc == 0 else '[FAIL]'} {label}")
    print(f"{'='*60}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
