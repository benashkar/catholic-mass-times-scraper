"""Tell Ben when the bulletin recovery stalls, so he is not the monitor.

The failure this exists to catch is not a crash -- crashes are loud. It is the
quiet one: on 2026-08-21 eight per-state walled passes were OOM-killed and
never relaunched, because the thing relaunching them lived inside an
interactive session. Every Render service still showed green. The daily cron
still fired on schedule. Nothing anywhere said "stopped". National coverage
moved +54 in 48 hours and the only reason anyone found out is that Ben asked.

So this watchdog does not check whether services are up. It checks whether the
NUMBER MOVED, which is the one signal that cannot be faked by a healthy-looking
process doing nothing.

Three things are asserted every run:

  1. national churches-with-a-PDF is higher than it was at the last check
  2. the walled sweep cron actually ran within the last 26 hours
  3. that run did not die (OOM or otherwise)

Any failure sends Telegram. A pass sends nothing -- per the standing rule that
a daily report which fires when everything is fine trains you to ignore it.

The previous reading lives in this service's own env var rather than a table:
db99 is shared, DDL there needs approval, and the mailbox machinery already
exists for exactly this.

Read-only against db99. Writes nothing but its own env var.

Usage (Render cron):
    python -u watchdog_bulletins.py
    python -u watchdog_bulletins.py --force-report   # send even when healthy
    python -u watchdog_bulletins.py --dry-run        # report, touch nothing
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SWEEP_CRON_ID = os.environ.get("SWEEP_CRON_ID", "crn-da43b7n10e5c73anojc0")
STATE_KEY = "WATCHDOG_BULLETIN_LAST"
MAX_RUN_AGE_H = float(os.environ.get("WATCHDOG_MAX_RUN_AGE_H", "26"))


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def national_with_pdf():
    """How many churches have at least one bulletin PDF, nationally."""
    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM church c
        WHERE c.website_url IS NOT NULL AND c.website_url <> ''
          AND EXISTS (SELECT 1 FROM bulletin_source bs
                      JOIN bulletin_pdf bp
                        ON bp.bulletin_source_id = bs.bulletin_source_id
                      WHERE bs.church_id = c.church_id)
        """
    )
    n = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) AS n FROM bulletin_pdf")
    pdfs = int(cur.fetchone()["n"])
    cur.close()
    conn.close()
    return n, pdfs


def _render(path):
    import requests

    key = os.environ.get("RENDER_API_KEY", "")
    r = requests.get(
        f"https://api.render.com/v1{path}",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def last_sweep_run():
    """(age_hours, status, detail) for the most recent scheduled sweep run.

    Cron RUNS are not jobs -- they surface only as service events. An earlier
    check that polled /jobs concluded the cron had never fired when in fact it
    had fired every day, so read events here and nothing else.
    """
    try:
        events = _render(f"/services/{SWEEP_CRON_ID}/events?limit=30")
    except Exception as e:
        return None, "api_error", str(e)[:200]

    for e in events:
        ev = e.get("event", e)
        if ev.get("type") != "cron_job_run_ended":
            continue
        ts = ev.get("timestamp", "")
        det = ev.get("details", {}) or {}
        status = det.get("status", "unknown")
        reason = det.get("reason", {}) or {}
        try:
            when = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None, status, "unparseable timestamp"
        age_h = (_utcnow() - when).total_seconds() / 3600.0
        detail = ""
        if reason.get("oomKilled"):
            detail = f"OOM at {reason['oomKilled'].get('memoryLimit')}"
        return age_h, status, detail
    return None, "no_runs", "no cron_job_run_ended event in the last 30 events"


def read_previous():
    try:
        raw = os.environ.get(STATE_KEY, "").strip()
        if not raw:
            return None, None
        ts, n = raw.split("|")[:2]
        return ts, int(n)
    except Exception:
        return None, None


def write_current(n):
    import requests

    key = os.environ.get("RENDER_API_KEY")
    service = os.environ.get("RENDER_SERVICE_ID")
    if not (key and service):
        print(
            "[ERR] cannot persist state: no RENDER_API_KEY/RENDER_SERVICE_ID",
            flush=True,
        )
        return False
    val = f"{_utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}|{n}"
    # Single-key PUT. The bulk PUT REPLACES every env var on the service, which
    # would strip PROXY_URL and the AWS credentials on the way past.
    r = requests.put(
        f"https://api.render.com/v1/services/{service}/env-vars/{STATE_KEY}",
        headers={"Authorization": f"Bearer {key}"},
        json={"value": val},
        timeout=30,
    )
    ok = r.status_code < 300
    print(
        f"[{'OK' if ok else 'ERR'}] persisted {STATE_KEY}={val} ({r.status_code})",
        flush=True,
    )
    return ok


def telegram(text):
    import requests

    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        print("[ERR] no telegram credentials; alert not sent", flush=True)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        print(f"[{'OK' if r.ok else 'ERR'}] telegram {r.status_code}", flush=True)
        return r.ok
    except Exception as e:
        print(f"[ERR] telegram: {e}", flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--force-report",
        action="store_true",
        help="Send the summary even when everything is healthy",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report but neither alert nor persist",
    )
    args = ap.parse_args()

    now, pdfs = national_with_pdf()
    prev_ts, prev_n = read_previous()
    age_h, status, detail = last_sweep_run()

    print(f"national with_pdf = {now:,}   bulletin_pdf rows = {pdfs:,}", flush=True)
    print(f"previous          = {prev_n} @ {prev_ts}", flush=True)
    age_s = "unknown" if age_h is None else f"{age_h:.1f}h"
    print(f"last sweep run    = {status} {age_s} ago {detail}", flush=True)

    problems = []

    # 1. Did the number move? This is the assertion that matters. A stall is
    #    only meaningful against a prior reading, so the first run of this
    #    watchdog establishes the baseline and asserts nothing.
    if prev_n is None:
        print("[--] no previous reading; establishing baseline", flush=True)
    elif now <= prev_n:
        problems.append(
            f"coverage has not moved since {prev_ts}: {prev_n:,} -> {now:,}"
        )

    # 2. Did the sweep actually run recently?
    if age_h is None:
        problems.append(f"cannot determine last sweep run ({status}: {detail})")
    elif age_h > MAX_RUN_AGE_H:
        problems.append(
            f"walled sweep has not run for {age_h:.1f}h (limit {MAX_RUN_AGE_H}h)"
        )

    # 3. Did it die? A single OOM is survivable by design -- the evening run
    #    resumes at the frontier the morning run left -- but it is still worth
    #    saying out loud, because two in a row means the frontier is stuck.
    if status not in ("successful", "no_runs") and age_h is not None:
        problems.append(f"last sweep run ended '{status}' {detail}".strip())

    gained = "" if prev_n is None else f" ({now - prev_n:+,} since last check)"
    body = (
        f"<b>bulletin recovery watchdog</b>\n"
        f"churches with a PDF: {now:,}{gained}\n"
        f"bulletin_pdf rows: {pdfs:,}\n"
        f"last sweep: {status}"
        + (f" {age_h:.1f}h ago" if age_h is not None else "")
        + (f" — {detail}" if detail else "")
    )

    if problems:
        alert = "⚠️ " + body + "\n\n" + "\n".join(f"• {p}" for p in problems)
        print("PROBLEMS:\n" + "\n".join(problems), flush=True)
        if not args.dry_run:
            telegram(alert)
    else:
        print("[OK] recovery is moving", flush=True)
        if args.force_report and not args.dry_run:
            telegram("✅ " + body)

    if not args.dry_run:
        write_current(now)

    # Exit non-zero on a stall so Render's own failure surface agrees with the
    # alert. A watchdog that always exits 0 is invisible in the dashboard, and
    # this repo's whole failure history is jobs that reported success while
    # doing nothing.
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
