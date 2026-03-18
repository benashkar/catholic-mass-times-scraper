"""
run_daily_pipeline.py — Daily pipeline for Render cron (stateless).

Scrapes mass times and extracts bulletin names directly to db99
(no local files), then rescores and triggers dashboard redeploy.

Usage:
    python run_daily_pipeline.py                    # All states
    python run_daily_pipeline.py --state OH         # One state
    python run_daily_pipeline.py --limit 50         # First 50 churches per state
    python run_daily_pipeline.py --skip-scrape      # Just rescore + redeploy
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def run_cmd(cmd, description, timeout_seconds=None):
    """Run a command, streaming output."""
    log(f"START: {description}")
    start = time.time()
    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, timeout=timeout_seconds)
        elapsed = time.time() - start
        if result.returncode == 0:
            log(f"  [OK] {description} ({elapsed:.0f}s)")
            return True
        else:
            log(f"  [ERR] {description} exited {result.returncode} ({elapsed:.0f}s)")
            return False
    except subprocess.TimeoutExpired:
        log(f"  [ERR] {description} timed out")
        return False
    except Exception as e:
        log(f"  [ERR] {description}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Daily pipeline (stateless)")
    parser.add_argument("--state", type=str, help="One state code (e.g. OH)")
    parser.add_argument("--limit", type=int, default=0, help="Max churches per state")
    parser.add_argument("--skip-scrape", action="store_true", help="Just rescore + redeploy")
    args = parser.parse_args()

    log("=" * 60)
    log("DAILY PIPELINE (Stateless -> db99)")
    log("=" * 60)

    start = time.time()
    results = {}

    # Step 1: Scrape mass times directly to db99
    if not args.skip_scrape:
        scrape_cmd = [sys.executable, "scrape_to_db99.py"]
        if args.state:
            scrape_cmd += ["--state", args.state]
        if args.limit:
            scrape_cmd += ["--limit", str(args.limit)]
        results["scrape"] = run_cmd(scrape_cmd, "Scrape mass times to db99", timeout_seconds=3600)

    # Step 2: Extract bulletin names directly to db99
    if not args.skip_scrape:
        bulletin_cmd = [sys.executable, "extract_bulletins_to_db99.py"]
        if args.state:
            bulletin_cmd += ["--state", args.state]
        if args.limit:
            bulletin_cmd += ["--limit", str(args.limit)]
        results["bulletins"] = run_cmd(bulletin_cmd, "Extract bulletin names to db99", timeout_seconds=7200)

    # Step 3: Rescore names + junk cleanup (skip full rescore on daily, just cleanup)
    results["rescore"] = run_cmd(
        [sys.executable, "rescore_names_sql.py", "--cleanup-only"],
        "Junk cleanup (SQL)", timeout_seconds=1200,
    )

    # Step 4: Health check
    results["health"] = run_cmd(
        [sys.executable, "tests/test_pipeline_health.py", "--quick"],
        "Health check", timeout_seconds=60,
    )

    # Step 5: Redeploy dashboard
    api_key = os.environ.get("RENDER_API_KEY", "")
    dashboard_id = "srv-d6li8dtm5p6s73chuh7g"
    if api_key:
        results["redeploy"] = run_cmd(
            ["curl", "-s", "-X", "POST",
             f"https://api.render.com/v1/services/{dashboard_id}/deploys",
             "-H", f"Authorization: Bearer {api_key}",
             "-H", "Content-Type: application/json",
             "-d", '{"clearCache":"do_not_clear"}'],
            "Trigger dashboard redeploy",
        )
    else:
        log("  [--] RENDER_API_KEY not set, skipping redeploy")
        results["redeploy"] = True

    elapsed = time.time() - start

    # Summary
    log("")
    log("=" * 60)
    log("DAILY PIPELINE SUMMARY")
    log("=" * 60)
    for step, ok in results.items():
        log(f"  {'[OK]' if ok else '[ERR]'} {step}")
    log(f"  Total: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    failed = [k for k, v in results.items() if not v]
    if failed:
        log(f"  FAILED: {', '.join(failed)}")
        return 1

    log("  All steps completed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
