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

# Same env var supervise_bulletin_sweep reads, so the two cannot drift: if the
# launcher and the "is it drained?" counter disagree on the window, a full
# queue reads as empty. See the comment on the bulletin step below for why this
# must stay shorter than the refresh period.
DAYS_FRESH = int(os.getenv("SWEEP_DAYS_FRESH", "6"))

# One in-process sweep runs ~15 churches/min at the 4 workers that fit in 2GB,
# so a 10h window covers ~9,000 of the ~17,000 due — a 2-week cycle against
# parishes that publish weekly. Sharded across containers it finishes in one
# night. Set BULLETIN_SHARDED=0 to fall back to the single-process path.
BULLETIN_SHARDED = os.getenv("BULLETIN_SHARDED", "1") == "1"


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


SWEEP_OK, SWEEP_NOTHING_DUE, SWEEP_FAILED = "ok", "nothing_due", "failed"

# How stale the oldest un-checked parish may get before the sweep is considered
# stalled. --days-fresh is 6 and the cron fires daily, so anything past 8 days
# means work is due and is not being picked up.
CORPUS_STALE_AFTER_DAYS = 8


def corpus_is_stale():
    """(stale, oldest_days) — has due work been sitting unclaimed too long?

    A per-run check cannot see this: a run that legitimately had nothing to do
    looks identical to a sweep that has silently stopped claiming work. This
    asks the opposite question — is anything overdue right now?
    """
    try:
        from rescore_names_sql import get_connection

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DATEDIFF(NOW(), MIN(c.bulletin_checked_at)) AS oldest_days
            FROM church c
            WHERE c.website_url IS NOT NULL AND c.website_url <> ''
              AND c.website_url NOT LIKE '%%facebook.com%%'
              AND c.website_url NOT LIKE '%%diocese%%'
              AND c.website_url NOT LIKE '%%archdiocese%%'
              AND EXISTS (SELECT 1 FROM bulletin_source bs
                          WHERE bs.church_id = c.church_id)
            """
        )
        oldest = (cur.fetchone() or {}).get("oldest_days")
        conn.close()
        if oldest is None:
            return False, None
        return oldest > CORPUS_STALE_AFTER_DAYS, int(oldest)
    except Exception as e:
        log(f"  [WARN] corpus freshness check failed: {e}")
        return False, None


def run_sharded_bulletin_sweep(deadline_minutes):
    """Drive the sharded sweep to completion, then return.

    The in-process path is capped by one container's memory: 4 workers is what
    fits in 2GB (12 was OOM-killed at ~240 churches), and 4 workers is ~15
    churches/min. The sweep supervisor already solves this by running disjoint
    shards as separate one-off jobs, each a fresh process — it just had no
    caller, so the weekly cron did the work single-threaded instead.

    Ticks the supervisor rather than launching once: shards die (OOM, a parish
    holding thousands of PDFs) and a dead shard contributes nothing until
    something restarts it. Progress survives a restart because
    church.bulletin_checked_at is stamped per church before it is processed.
    """
    import supervise_bulletin_sweep as sweep

    if not os.environ.get("RENDER_API_KEY"):
        log("  [--] RENDER_API_KEY unset; falling back to the in-process sweep")
        return None

    deadline = time.time() + deadline_minutes * 60
    tick = 0
    started_empty = None
    while time.time() < deadline:
        tick += 1
        try:
            remaining = sweep.queue_remaining()
        except Exception as e:
            log(f"  [ERR] queue check failed: {e}")
            return SWEEP_FAILED

        # On a daily cron most days have nothing due, and that is a legitimate
        # outcome — but it must not be reported as a successful sweep. A
        # trivially-true OK is exactly what hid the original failure for weeks.
        if started_empty is None:
            started_empty = remaining == 0
            if started_empty:
                log("  [--] nothing due; no sweep needed this run")
                return SWEEP_NOTHING_DUE

        if remaining == 0:
            live = [s for s, j in sweep.latest_per_shard("--known-sources-only").items()
                    if j.get("status") in ("running", "pending")]
            if not live:
                log(f"  [OK] sweep drained after {tick} tick(s)")
                return SWEEP_OK
            log(f"  queue drained; {len(live)} shard(s) still finishing")
        else:
            latest = sweep.latest_per_shard("--known-sources-only")
            relaunched = idle = 0
            for shard in range(sweep.SHARDS):
                j = latest.get(shard)
                if j and j.get("status") in ("running", "pending"):
                    continue
                # Work is partitioned by MOD(church_id, SHARDS), so partitions
                # drain at different rates. Relaunching purely because a shard
                # is "not running" restarts already-empty partitions on every
                # tick — on 2026-08-16 that was ~2 containers every 2 minutes,
                # each loading spaCy and exiting within a minute having found
                # nothing, for the last hour of the sweep.
                try:
                    if sweep.queue_remaining(shard=shard, shards=sweep.SHARDS) == 0:
                        idle += 1
                        continue
                except Exception as e:
                    log(f"  [WARN] shard {shard} queue check failed, launching anyway: {e}")
                try:
                    sweep.api(f"/services/{sweep.CRON_ID}/jobs", "POST",
                              {"startCommand": sweep.shard_command(shard)})
                    relaunched += 1
                except Exception as e:
                    log(f"  [ERR] shard {shard}: {e}")
            log(f"  tick {tick}: {remaining:,} due, {relaunched} launched"
                + (f", {idle} partition(s) already empty" if idle else ""))

        time.sleep(120)

    log(f"  [--] deadline reached after {tick} tick(s); shards keep running")
    return SWEEP_OK


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
    # --days-fresh 6: skip churches checked in the last 6 days (SQL-side, on
    # church.bulletin_checked_at) so each run advances the rotation frontier.
    #
    # This MUST stay shorter than the cron period. At 14 days on a weekly cron
    # a church checked last Tuesday was still "fresh" this Tuesday, so it was
    # skipped: the Aug 11 run drained its queue in 14 minutes against a 10h
    # budget and touched 378 churches. The bulk of the corpus then waited a
    # further two weeks, giving an effective ~3-week refresh on parishes that
    # publish a NEW bulletin every week — 2 of every 3 editions were missed
    # (bulletin_pdf: ~1,600/wk through July, 43 for the week of Aug 3).
    # 6 mirrors the mass-times cron's --stale-days 6 for the same reason.
    #
    # --max-runtime-minutes: exit cleanly inside the timeout below, keeping the
    # watermark, instead of being SIGKILLed mid-batch as it was every week.
    # PDF extraction itself already skips via text_extracted=1.
    nothing_was_due = False
    if not args.skip_scrape:
        # A whole-corpus sweep goes through the shard supervisor; --state and
        # --limit are targeted runs, so those stay in-process.
        sharded = BULLETIN_SHARDED and not args.state and not args.limit
        swept = run_sharded_bulletin_sweep(BULLETIN_RUNTIME_MINUTES) if sharded else None
        if swept == SWEEP_NOTHING_DUE:
            # Not a success and not a failure. The cron fires daily but
            # --days-fresh is 6, so most days there is genuinely nothing to
            # sweep; calling that OK would make a stalled sweep and a quiet day
            # look identical, which is the whole bug this pipeline keeps hitting.
            nothing_was_due = True
            results["bulletins"] = True
        elif swept is not None:
            results["bulletins"] = swept == SWEEP_OK

    if not args.skip_scrape and "bulletins" not in results:
        bulletin_cmd = [
            sys.executable,
            "extract_bulletins_to_db99.py",
            "--days-fresh", str(DAYS_FRESH),
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
    # Scoped to THIS run, not a trailing week: on 2026-08-11 the bulletin cron
    # drained its queue in 14 minutes and touched 378 churches, but --days 7
    # still saw the 47k PDFs a manual sweep had pulled on Aug 6 and reported
    # OK. A trailing window lets yesterday's work vouch for today's dead run.
    if nothing_was_due:
        # A no-op run cannot clear per-run floors, so asserting on them would
        # fail every quiet day. Ask the question that still has meaning: is
        # anything overdue? That is what a permanently stalled sweep looks
        # like, and no per-run check can see it.
        stale, oldest = corpus_is_stale()
        if stale:
            log(f"  [ERR] nothing due, but the oldest parish was checked "
                f"{oldest} days ago (> {CORPUS_STALE_AFTER_DAYS}) — sweep is stalled")
            results["verify"] = False
        else:
            log(f"  [OK] nothing due; oldest parish checked {oldest} day(s) ago")
            results["verify"] = True
    else:
        run_minutes = max(int((time.time() - start) / 60) + 5, 10)
        results["verify"] = run_cmd(
            [sys.executable, "verify_bulletin_run.py", "--since-minutes", str(run_minutes)],
            "Verify bulletin run + Telegram report",
            timeout_seconds=300,
        )

    elapsed = time.time() - start

    # Non-critical steps: failures logged as warnings, don't cause exit code 1.
    #
    # "bulletins" used to live here because the step exited 1 on a single
    # unreachable parish, which made a red status meaningless. Now that its
    # exit code only fires on a run that produced nothing (or errored on >25%
    # of churches), a bulletin failure is real and must fail the pipeline —
    # masking it is what let bulletins=FAIL run unnoticed from 2026-07-14 to
    # 2026-08-04 while every run still logged "completed".
    non_critical = {"backfill", "health", "redeploy"}

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
    step_summary = ", ".join(
        f"{k}=" + ("SKIPPED(nothing due)" if k == "bulletins" and nothing_was_due
                   else ("OK" if v else "FAIL"))
        for k, v in results.items()
    )
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
