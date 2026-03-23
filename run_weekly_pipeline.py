#!/usr/bin/env python3
"""
run_weekly_pipeline.py -- Tuesday weekly pipeline orchestrator.

Runs the full scrape-to-database pipeline:
  1. Re-scrape all 50 states (detail-only, resume-safe)
  2. Regenerate 12-week dated services CSVs
  3. Run bulletin pipeline (discover + download + extract new PDFs)
  4. Sync all data to db99 (UPSERT)
  5. Git commit + push (triggers dashboard auto-deploy)

Usage:
    python run_weekly_pipeline.py                # Full pipeline
    python run_weekly_pipeline.py --scrape-only  # Steps 1-3 only
    python run_weekly_pipeline.py --sync-only    # Step 4 only
    python run_weekly_pipeline.py --states ohio texas  # Specific states
    python run_weekly_pipeline.py --skip-bulletins     # Skip bulletin step
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

# All 50 states
ALL_STATES = [
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new_hampshire",
    "new_jersey",
    "new_mexico",
    "new_york",
    "north_carolina",
    "north_dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode_island",
    "south_carolina",
    "south_dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west_virginia",
    "wisconsin",
    "wyoming",
]

# States per batch for bulletin scraping (10 states per batch, 3 parallel)
BULLETIN_BATCH_SIZE = 10
BULLETIN_MAX_PARALLEL = 3


def log(msg):
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def run_cmd(cmd, description, timeout_seconds=None):
    """Run a shell command, streaming output. Returns True on success."""
    log(f"START: {description}")
    log(f"  cmd: {' '.join(cmd)}")
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            timeout=timeout_seconds,
        )
        elapsed = time.time() - start
        if result.returncode == 0:
            log(f"  OK: {description} ({elapsed:.0f}s)")
            return True
        else:
            log(f"  WARN: {description} exited with code {result.returncode} ({elapsed:.0f}s)")
            return False
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        log(f"  TIMEOUT: {description} after {elapsed:.0f}s")
        return False
    except Exception as e:
        log(f"  ERR: {description}: {e}")
        return False


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def step_scrape_details(states):
    """Step 1: Re-scrape church detail pages for all states."""
    log("=" * 60)
    log("STEP 1: Re-scrape church details (all states)")
    log("=" * 60)

    # Process states in batches of 5 to avoid overloading CatholicIndex
    batch_size = 5
    all_ok = True

    for i in range(0, len(states), batch_size):
        batch = states[i : i + batch_size]
        cmd = [sys.executable, "run_statewide.py", *batch, "--detail-only", "--resume"]
        # 2 hours per batch of 5 states
        ok = run_cmd(cmd, f"Detail scrape: {', '.join(batch)}", timeout_seconds=7200)
        if not ok:
            all_ok = False

    return all_ok


def step_regenerate_dates(states):
    """Step 2: Regenerate 12-week dated services CSVs."""
    log("=" * 60)
    log("STEP 2: Regenerate 12-week dated services")
    log("=" * 60)

    cmd = [sys.executable, "run_statewide.py", *states, "--dates-only"]
    # Fast step - just date math, no network. 10 min should be plenty.
    return run_cmd(cmd, "Regenerate dated_services.csv (all states)", timeout_seconds=600)


def step_bulletins(states):
    """Step 3: Run bulletin pipeline (discover + download + extract)."""
    log("=" * 60)
    log("STEP 3: Bulletin pipeline (discover + download + extract)")
    log("=" * 60)

    all_ok = True

    for i in range(0, len(states), BULLETIN_BATCH_SIZE):
        batch = states[i : i + BULLETIN_BATCH_SIZE]
        # Run bulletin scraper for each state in this batch sequentially
        # (the scraper itself handles one state at a time)
        for state in batch:
            cmd = [sys.executable, "run_bulletin_scraper.py", "all", state, "--resume"]
            # 2 hours per state max
            ok = run_cmd(cmd, f"Bulletins: {state}", timeout_seconds=7200)
            if not ok:
                all_ok = False

    return all_ok


def step_sync_db():
    """Step 4: Sync all scraped data to db99."""
    log("=" * 60)
    log("STEP 4: Sync to db99 (UPSERT)")
    log("=" * 60)

    cmd = [sys.executable, "sync_to_db99.py"]
    # DB sync is fast - ~5 min for all 50 states
    return run_cmd(cmd, "Sync to db99", timeout_seconds=600)


def step_rescore_names():
    """Step 4b: Re-score names and clean junk using SQL on db99."""
    log("=" * 60)
    log("STEP 4b: Re-score names + junk cleanup (SQL)")
    log("=" * 60)

    cmd = [sys.executable, "rescore_names_sql.py"]
    return run_cmd(cmd, "Re-score names via SQL", timeout_seconds=300)


def step_git_push():
    """Step 5: Git commit updated data and push to trigger dashboard deploy."""
    log("=" * 60)
    log("STEP 5: Git commit + push")
    log("=" * 60)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d")

    # Stage data files (CSVs and JSONL, not PDFs or progress files)
    run_cmd(
        [
            "git",
            "add",
            "data/output/*/all_services.csv",
            "data/output/*/dated_services.csv",
            "data/output/*/church_details.jsonl",
            "data/output/*/parsed_addresses.csv",
            "data/output/*/bulletin_names.csv",
            "data/churches/",
        ],
        "Git add data files",
    )

    # Check if there are staged changes
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PROJECT_ROOT)

    if result.returncode == 0:
        log("  No changes to commit")
        return True

    commit_msg = (
        f"Weekly pipeline refresh ({timestamp})\n\n"
        f"Automated Tuesday pipeline: re-scraped details, regenerated 12-week dates,\n"
        f"updated bulletin names, synced to db99.\n\n"
        f"Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
    )

    ok = run_cmd(["git", "commit", "-m", commit_msg], "Git commit")
    if not ok:
        return False

    run_cmd(["git", "pull", "--rebase"], "Git pull --rebase")

    return run_cmd(["git", "push"], "Git push")


def step_validate():
    """Step 4c: Run health checks to verify data quality."""
    log("=" * 60)
    log("STEP 4c: Post-sync health check")
    log("=" * 60)

    cmd = [sys.executable, "tests/test_pipeline_health.py", "--quick"]
    return run_cmd(cmd, "Pipeline health check", timeout_seconds=60)


def step_redeploy_dashboard():
    """Step 6: Trigger Render dashboard redeploy via API."""
    log("=" * 60)
    log("STEP 6: Trigger dashboard redeploy")
    log("=" * 60)

    api_key = os.environ.get("RENDER_API_KEY", "")
    dashboard_id = "srv-d6li8dtm5p6s73chuh7g"

    if not api_key:
        log("  [--] RENDER_API_KEY not set, skipping redeploy trigger")
        return True

    cmd = [
        "curl",
        "-s",
        "-X",
        "POST",
        f"https://api.render.com/v1/services/{dashboard_id}/deploys",
        "-H",
        f"Authorization: Bearer {api_key}",
        "-H",
        "Content-Type: application/json",
        "-d",
        '{"clearCache":"do_not_clear"}',
    ]
    return run_cmd(cmd, "Trigger dashboard redeploy")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Weekly Tuesday pipeline orchestrator")
    parser.add_argument(
        "--scrape-only", action="store_true", help="Run scrape steps only (1-3), skip DB sync"
    )
    parser.add_argument("--sync-only", action="store_true", help="Run DB sync only (step 4)")
    parser.add_argument(
        "--skip-bulletins", action="store_true", help="Skip bulletin scraping (step 3)"
    )
    parser.add_argument(
        "--skip-scrape", action="store_true", help="Skip scraping (steps 1-3), run sync + deploy"
    )
    parser.add_argument(
        "--states", nargs="+", default=None, help="Specific states to process (default: all 50)"
    )
    args = parser.parse_args()

    states = args.states if args.states else ALL_STATES

    log("=" * 60)
    log("WEEKLY TUESDAY PIPELINE")
    log(f"States: {len(states)}")
    log(f"Mode: {'scrape-only' if args.scrape_only else 'sync-only' if args.sync_only else 'full'}")
    log("=" * 60)

    pipeline_start = time.time()
    results = {}

    if args.sync_only:
        results["sync_db"] = step_sync_db()
        results["rescore"] = step_rescore_names()
        results["validate"] = step_validate()
        results["redeploy"] = step_redeploy_dashboard()
    elif args.scrape_only:
        results["scrape_details"] = step_scrape_details(states)
        results["regenerate_dates"] = step_regenerate_dates(states)
        if not args.skip_bulletins:
            results["bulletins"] = step_bulletins(states)
    elif args.skip_scrape:
        results["sync_db"] = step_sync_db()
        results["rescore"] = step_rescore_names()
        results["validate"] = step_validate()
        results["git_push"] = step_git_push()
        results["redeploy"] = step_redeploy_dashboard()
    else:
        # Full pipeline
        results["scrape_details"] = step_scrape_details(states)
        results["regenerate_dates"] = step_regenerate_dates(states)
        if not args.skip_bulletins:
            results["bulletins"] = step_bulletins(states)
        results["sync_db"] = step_sync_db()
        results["rescore"] = step_rescore_names()
        results["validate"] = step_validate()
        results["git_push"] = step_git_push()
        results["redeploy"] = step_redeploy_dashboard()

    total_elapsed = time.time() - pipeline_start

    # Summary
    log("")
    log("=" * 60)
    log("PIPELINE SUMMARY")
    log("=" * 60)
    for step_name, ok in results.items():
        status = "[OK]" if ok else "[ERR]"
        log(f"  {status} {step_name}")
    log(f"  Total time: {total_elapsed:.0f}s ({total_elapsed/3600:.1f} hours)")

    failed = [k for k, v in results.items() if not v]
    if failed:
        log(f"  FAILED STEPS: {', '.join(failed)}")
        return 1

    log("  All steps completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
