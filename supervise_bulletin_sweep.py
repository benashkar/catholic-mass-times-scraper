"""
supervise_bulletin_sweep.py — keep the sharded bulletin sweep alive.

Shards die. On the 2GB cron plan a worker holding a big PDF inside pdfplumber,
alongside the spaCy model, can exhaust memory and Render kills the job with no
runtime logs. A dead shard contributes nothing until something restarts it, and
"something" should not be a human watching a terminal at 2am.

This runs on a cron: it looks at the most recent job per shard and relaunches
any that is not currently running. Restarting is cheap and safe because
church.bulletin_checked_at is stamped per church, so a relaunched shard resumes
at the frontier instead of redoing work.

Exits 0 when all shards are running or were relaunched.

Usage:
    python supervise_bulletin_sweep.py                # relaunch dead shards
    python supervise_bulletin_sweep.py --dry-run
    python supervise_bulletin_sweep.py --stop         # stand down, no relaunch
"""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.telegram import send_telegram

API = "https://api.render.com/v1"
CRON_ID = "crn-d6s8t02a214c73bt62s0"          # church-bulletin-cron
SHARDS = int(os.environ.get("SWEEP_SHARDS", "6"))
WORKERS = int(os.environ.get("SWEEP_WORKERS", "4"))
# Memory budget, not politeness: these two keep a shard inside 2GB.
PDF_CAP = os.environ.get("SWEEP_PDF_CAP", "150")
PDF_SIZE_MB = os.environ.get("SWEEP_PDF_SIZE_MB", "12")
RUNTIME_MIN = os.environ.get("SWEEP_RUNTIME_MIN", "600")


def api(path, method="GET", payload=None):
    key = os.environ["RENDER_API_KEY"]
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    raw = urllib.request.urlopen(req).read().decode()
    return json.loads(raw) if raw.strip() else {}


def shard_command(shard):
    """Bare argv — Render does NOT run startCommand through a shell.

    An env-var prefix ("FOO=1 python ...") is shell syntax: Render treats FOO=1
    as the executable and the job dies within seconds, which looked exactly like
    an OOM and cost a long detour. Memory tuning (MAX_PDFS_PER_CHURCH,
    MAX_PDF_SIZE_MB, NER_MODEL) lives in the SERVICE env vars, which one-off
    jobs inherit. For the same reason, no `sh -c "..."` and no shell operators.
    """
    return (
        f"python -u extract_bulletins_to_db99.py --known-sources-only --days-fresh 14 "
        f"--shards {SHARDS} --shard {shard} --workers {WORKERS} "
        f"--batch-size 25 --max-runtime-minutes {RUNTIME_MIN}"
    )


def latest_per_shard():
    """Most recent job per shard, from the sweep's own start commands."""
    jobs = api(f"/services/{CRON_ID}/jobs?limit=60")
    latest = {}
    for row in jobs:
        j = row.get("job", row)
        sc = j.get("startCommand", "") or ""
        if "--shard " not in sc or "extract_bulletins_to_db99" not in sc:
            continue
        try:
            shard = int(sc.split("--shard ")[1].split()[0])
        except (IndexError, ValueError):
            continue
        # /jobs comes back newest-first, so the first sighting wins.
        latest.setdefault(shard, j)
    return latest


def queue_remaining():
    """Productive churches still due. 0 means the sweep has drained."""
    from src.utils.db_connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) v FROM church c
                WHERE website_url IS NOT NULL AND website_url != ''
                  AND website_url NOT LIKE '%%facebook.com%%'
                  AND website_url NOT LIKE '%%diocese%%'
                  AND website_url NOT LIKE '%%archdiocese%%'
                  AND (bulletin_checked_at IS NULL
                       OR bulletin_checked_at < NOW() - INTERVAL 14 DAY)
                  AND EXISTS (SELECT 1 FROM bulletin_source bs
                              WHERE bs.church_id = c.church_id)
                """
            )
            return cur.fetchone()["v"]


def finish_sweep():
    """Score the harvest and refresh the dashboard once the queue has drained.

    extract_bulletins_to_db99.py inserts every name at a provisional 'low';
    rescore_names_sql.py is what actually assigns confidence from the SSA and
    Census reference data. run_daily_pipeline.py does this after its own run,
    but an ad-hoc sharded sweep does not — which left 646,157 recovered names
    sitting at 100% low until it was run by hand. Doing it here means the sweep
    finishes itself.
    """
    import subprocess

    root = os.path.dirname(os.path.abspath(__file__))
    steps = [
        ("rescore", [sys.executable, "rescore_names_sql.py", "--new-only"], 2400),
        ("refresh-stats", [sys.executable, "rescore_names_sql.py", "--refresh-stats"], 1800),
        # The coverage report is the actual proof the recovery worked: it shows
        # whether the 2026-02-23 -> 2026-08-05 hole refilled. Telegram it so the
        # answer is waiting rather than needing someone to go and run it.
        ("coverage-report",
         [sys.executable, "report_edition_coverage.py", "--weeks", "40", "--telegram"], 1800),
    ]
    done = []
    for label, cmd, timeout in steps:
        print(f"  [sweep-complete] {label}...", flush=True)
        try:
            rc = subprocess.run(cmd, cwd=root, timeout=timeout).returncode
        except Exception as e:
            print(f"  [ERR] {label}: {e}")
            rc = 1
        done.append(f"{label}={'OK' if rc == 0 else 'FAIL'}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop", action="store_true", help="Do not relaunch anything")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    # If the queue has drained and nothing is still running, the sweep is done:
    # score it, refresh the dashboard, and say so. Checked BEFORE relaunching so
    # a drained queue does not get six more shards with nothing to do.
    try:
        remaining = queue_remaining()
    except Exception as e:
        print(f"  [WARN] queue check failed: {e}")
        remaining = None

    if remaining == 0 and not args.dry_run and not args.stop:
        live_now = [s for s, j in latest_per_shard().items()
                    if j.get("status") in ("running", "pending")]
        if not live_now:
            print("Queue drained and no shards running — finishing the sweep.")
            done = finish_sweep()
            msg = ("<b>Bulletin recovery sweep COMPLETE</b>\n"
                   "productive queue drained\n" + "\n".join(done))
            print(msg.replace("<b>", "").replace("</b>", ""))
            if not args.no_telegram:
                send_telegram(msg)
            return 0
        print(f"Queue drained; {len(live_now)} shard(s) still finishing.")
        return 0

    if remaining is not None:
        print(f"Productive queue remaining: {remaining:,}")

    latest = latest_per_shard()
    running, relaunched, failed = [], [], []

    for shard in range(SHARDS):
        j = latest.get(shard)
        status = j.get("status") if j else "never-started"
        # "pending" is a job that has been accepted but not started yet. Treating
        # it as dead would launch a second job for the same shard every tick and
        # pile up duplicates that compete for the same resources.
        if status in ("running", "pending"):
            running.append(shard)
            continue
        if args.stop:
            failed.append((shard, status))
            continue
        if args.dry_run:
            print(f"  [DRY] would relaunch shard {shard} (was {status})")
            relaunched.append(shard)
            continue
        try:
            d = api(f"/services/{CRON_ID}/jobs", "POST", {"startCommand": shard_command(shard)})
            print(f"  [OK] relaunched shard {shard} (was {status}) -> {d.get('id')}")
            relaunched.append(shard)
        except Exception as e:
            print(f"  [ERR] shard {shard}: {e}")
            failed.append((shard, str(e)[:80]))

    msg = (
        f"<b>Bulletin sweep supervisor</b>\n"
        f"running: {sorted(running)}\n"
        f"relaunched: {sorted(relaunched)}\n"
        f"problems: {failed if failed else 'none'}"
    )
    print(msg.replace("<b>", "").replace("</b>", ""))

    # Only ping when something needed intervention; a fully healthy sweep is
    # reported by verify_bulletin_run.py, not by every supervisor tick.
    if relaunched and not args.no_telegram and not args.dry_run:
        send_telegram(msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
