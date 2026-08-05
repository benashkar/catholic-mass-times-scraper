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
from datetime import UTC, datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

# How long the bulletin crawl gets. The bulletin cron is weekly (Tue 03:00 UTC)
# and nothing else competes for the window, so give it a real one — the old 2h
# cap killed the step mid-California every week (bulletins=FAIL since Jul 14).
# Override with BULLETIN_RUNTIME_MINUTES on the Render service.
BULLETIN_RUNTIME_MINUTES = int(os.getenv("BULLETIN_RUNTIME_MINUTES", "600"))


def log(msg):
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
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
    parser.add_argument(
        "--bulletins-only",
        action="store_true",
        help="Skip mass times scrape, only run bulletins + rescore + redeploy",
    )
    args = parser.parse_args()

    log("=" * 60)
    log("DAILY PIPELINE (Stateless -> db99)")
    log("=" * 60)

    start = time.time()
    results = {}

    # Step 1: Scrape mass times directly to db99
    if not args.skip_scrape and not args.bulletins_only:
        scrape_cmd = [sys.executable, "scrape_to_db99.py"]
        if args.state:
            scrape_cmd += ["--state", args.state]
        if args.limit:
            scrape_cmd += ["--limit", str(args.limit)]
        results["scrape"] = run_cmd(scrape_cmd, "Scrape mass times to db99", timeout_seconds=3600)

    # Step 2: Extract bulletin names directly to db99
    # --days-fresh 14: skip churches checked in the last 14 days (SQL-side, on
    # church.bulletin_checked_at) so each run advances the rotation frontier.
    # --max-runtime-minutes: exit cleanly inside the timeout below, keeping the
    # watermark, instead of being SIGKILLed mid-batch as it was every week.
    # PDF extraction itself already skips via text_extracted=1.
    if not args.skip_scrape:
        bulletin_cmd = [
            sys.executable,
            "extract_bulletins_to_db99.py",
            "--days-fresh", "14",
            "--max-runtime-minutes", str(BULLETIN_RUNTIME_MINUTES),
        ]
        if args.state:
            bulletin_cmd += ["--state", args.state]
        if args.limit:
            bulletin_cmd += ["--limit", str(args.limit)]
        results["bulletins"] = run_cmd(
            bulletin_cmd,
            "Extract bulletin names to db99",
            timeout_seconds=(BULLETIN_RUNTIME_MINUTES + 20) * 60,
        )

    # Step 2b: Backfill empty fields from context (Layer 2 self-healing)
    results["backfill"] = run_cmd(
        [sys.executable, "backfill_empty_fields.py"],
        "Backfill empty fields from context",
        timeout_seconds=120,
    )

    # Step 3: Rescore only new names + junk cleanup
    results["rescore"] = run_cmd(
        [sys.executable, "rescore_names_sql.py", "--new-only"],
        "Rescore new names (SQL)",
        timeout_seconds=2400,
    )

    # Step 3b: Rebuild dashboard stats (--new-only skips this in rescore)
    results["refresh_stats"] = run_cmd(
        [sys.executable, "rescore_names_sql.py", "--refresh-stats"],
        "Refresh bulletin_state_stats",
        timeout_seconds=900,
    )

    # Step 4: Health check
    results["health"] = run_cmd(
        [sys.executable, "tests/test_pipeline_health.py", "--quick"],
        "Health check",
        timeout_seconds=60,
    )

    # Step 5: Redeploy dashboard
    api_key = os.environ.get("RENDER_API_KEY", "")
    dashboard_id = "srv-d6li8dtm5p6s73chuh7g"
    if api_key:
        results["redeploy"] = run_cmd(
            [
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
            ],
            "Trigger dashboard redeploy",
        )
    else:
        log("  [--] RENDER_API_KEY not set, skipping redeploy")
        results["redeploy"] = True

    # Step 6: Prove the scraper actually did work, and say so on Telegram.
    # Runs LAST and unconditionally, including when the bulletin step failed —
    # that is the case it exists to catch. For four months this pipeline logged
    # "completed" every week while the bulletin step was being killed partway,
    # because a green exit code says nothing about whether rows landed.
    results["verify"] = run_cmd(
        [sys.executable, "verify_bulletin_run.py", "--days", "7"],
        "Verify bulletin run + Telegram report",
        timeout_seconds=300,
    )

    elapsed = time.time() - start

    # Non-critical steps: failures logged as warnings, don't cause exit code 1.
    # Bulletins may partially fail (some churches unreachable) but still extract names.
    non_critical = {"bulletins", "backfill", "health", "redeploy"}

    # Summary
    log("")
    log("=" * 60)
    log("DAILY PIPELINE SUMMARY")
    log("=" * 60)
    for step, ok in results.items():
        tag = "[OK]" if ok else ("[WARN]" if step in non_critical else "[ERR]")
        log(f"  {tag} {step}")
    log(f"  Total: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    critical_failed = [k for k, v in results.items() if not v and k not in non_critical]
    warn_failed = [k for k, v in results.items() if not v and k in non_critical]

    if warn_failed:
        log(f"  NON-CRITICAL WARNINGS: {', '.join(warn_failed)}")
    if critical_failed:
        log(f"  FAILED: {', '.join(critical_failed)}")

    # Log step results to scrape_log for remote diagnosis
    step_summary = ", ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in results.items())
    try:
        from rescore_names_sql import get_connection

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO scrape_log (scrape_type, completed_at, status, notes) "
            "VALUES ('daily_pipeline_steps', NOW(), %s, %s)",
            ("failed" if critical_failed else "completed", step_summary[:500]),
        )
        conn.close()
    except Exception:
        pass

    if critical_failed:
        return 1

    log("  All critical steps completed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
