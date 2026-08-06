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
    return (
        f"MAX_PDFS_PER_CHURCH={PDF_CAP} MAX_PDF_SIZE_MB={PDF_SIZE_MB} "
        f"NER_MODEL=en_core_web_sm "
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop", action="store_true", help="Do not relaunch anything")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    latest = latest_per_shard()
    running, relaunched, failed = [], [], []

    for shard in range(SHARDS):
        j = latest.get(shard)
        status = j.get("status") if j else "never-started"
        if status == "running":
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
