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

# A stall does not have to be a zero. The failure this watchdog was written for
# was national coverage moving +54 in 48 hours while every service showed green
# -- a crawl, not a stop. "> 0" would have passed it silently, so the assertion
# has to be a RATE.
#
# A FIXED rate is wrong in the other direction. The gap is 9,532 today and a
# bar of 100/day is fair; when the gap is down to 400 the same bar fires every
# single morning and gets muted, which is how a watchdog dies. So the bar is a
# fraction of the work REMAINING -- 1% of the gap per day -- which is a
# demanding target early, relaxes as the corpus saturates, and never asks for
# more churches than are actually left to find.
#
# Calibration: at the 08-21 stall the gap was ~9,600, so the bar was ~96/day
# and the observed +54 over 48h (needing 192) alerts, which is the whole point.
GAP_FRACTION_PER_DAY = float(os.environ.get("WATCHDOG_GAP_FRACTION", "0.01"))
# Below this the percentage becomes noise; a small absolute floor keeps the
# check meaningful without being shrill.
MIN_GAIN_FLOOR = float(os.environ.get("WATCHDOG_MIN_GAIN_FLOOR", "5"))
# Shorter than this and the rate is not worth judging -- see the guard in
# main(). Well under the 24h schedule, so a real daily run always judges.
MIN_ELAPSED_H = float(os.environ.get("WATCHDOG_MIN_ELAPSED_H", "6"))


def _utcnow():
    return datetime.datetime.now(datetime.UTC)


def _elapsed_hours(ts):
    """Hours since an ISO stamp, or 24.0 if it cannot be read.

    Falling back to 24 rather than 0 matters: 0 would make the required gain 0
    and turn an unreadable timestamp into a silent pass, which is the exact
    class of bug this file exists to catch.
    """
    try:
        when = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return max((_utcnow() - when).total_seconds() / 3600.0, 0.0)
    except Exception:
        return 24.0


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
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM church c
        WHERE c.website_url IS NOT NULL AND c.website_url <> ''
          AND EXISTS (SELECT 1 FROM bulletin_source bs
                      WHERE bs.church_id = c.church_id)
        """
    )
    with_source = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) AS n FROM bulletin_pdf")
    pdfs = int(cur.fetchone()["n"])
    cur.close()
    conn.close()
    return n, pdfs, with_source


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


def proxy_health():
    """(ok, detail) for the residential proxy the walled passes depend on.

    Added after the proxy was found returning 407 while sixteen walled passes
    ran against it. Nothing broke loudly: walled hosts 403 exactly as they do
    when they are simply blocked, the passes exit 0, and the only visible
    symptom is coverage crawling -- which is the same symptom as a dozen other
    causes. A direct check names it in one line instead.

    Checked separately from the rate assertion because a dead proxy is worth
    saying even on a day the number moved: direct-fetchable churches keep the
    total climbing while every walled host silently yields nothing.
    """
    proxy = os.environ.get("PROXY_URL", "").strip()
    if not proxy:
        return None, "PROXY_URL not set on this service"
    try:
        import requests

        r = requests.get(
            "http://api.ipify.org",
            proxies={"http": proxy, "https": proxy},
            timeout=30,
        )
        if r.status_code == 407:
            return False, "407 Proxy Authentication Required (account/balance)"
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}"
        return True, f"exit ip {r.text.strip()[:24]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"


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

    now, pdfs, with_source = national_with_pdf()
    gap = with_source - now
    prev_ts, prev_n = read_previous()
    age_h, status, detail = last_sweep_run()

    print(f"national with_pdf = {now:,}   bulletin_pdf rows = {pdfs:,}", flush=True)
    print(f"remaining gap     = {gap:,}", flush=True)
    print(f"previous          = {prev_n} @ {prev_ts}", flush=True)
    age_s = "unknown" if age_h is None else f"{age_h:.1f}h"
    print(f"last sweep run    = {status} {age_s} ago {detail}", flush=True)

    problems = []

    # 1. Did the number move? This is the assertion that matters. A stall is
    #    only meaningful against a prior reading, so the first run of this
    #    watchdog establishes the baseline and asserts nothing.
    if prev_n is None:
        print("[--] no previous reading; establishing baseline", flush=True)
    else:
        # Compare against a RATE, not against zero, and scale by the actual
        # elapsed time rather than assuming the check ran exactly a day ago --
        # a watchdog that skipped a run would otherwise be handed twice the
        # budget and stay quiet through a real stall.
        elapsed_h = _elapsed_hours(prev_ts)
        gain = now - prev_n
        per_day = max(gap * GAP_FRACTION_PER_DAY, MIN_GAIN_FLOOR)
        required = per_day * (elapsed_h / 24.0)
        print(
            f"gain              = {gain:+,} over {elapsed_h:.1f}h "
            f"(bar {per_day:.0f}/day -> need >= {required:.0f})",
            flush=True,
        )
        if elapsed_h < MIN_ELAPSED_H:
            # Too short a window to judge. A run six minutes after the last one
            # sets the bar at 0.4 churches and a gain of 0 "fails" -- which is
            # exactly what happened the first time this shipped, on a manual
            # test run. Churches arrive in bursts as each state pass finishes,
            # so a few minutes of flat is normal and alerting on it is how a
            # watchdog earns a mute.
            print(
                f"[--] only {elapsed_h:.2f}h since the last check "
                f"(need {MIN_ELAPSED_H}h) — not judging the rate",
                flush=True,
            )
        elif gain < required:
            problems.append(
                f"recovery has stalled: {gain:+,} churches in {elapsed_h:.1f}h "
                f"({prev_n:,} -> {now:,}), expected at least {required:.0f} "
                f"({per_day:.0f}/day against a {gap:,} gap)"
            )

    # 2. Did the sweep actually run recently?
    if age_h is None:
        problems.append(f"cannot determine last sweep run ({status}: {detail})")
    elif age_h > MAX_RUN_AGE_H:
        problems.append(f"walled sweep has not run for {age_h:.1f}h (limit {MAX_RUN_AGE_H}h)")

    # 3. Did it die? A single OOM is survivable by design -- the evening run
    #    resumes at the frontier the morning run left -- but it is still worth
    #    saying out loud, because two in a row means the frontier is stuck.
    if status not in ("successful", "no_runs") and age_h is not None:
        problems.append(f"last sweep run ended '{status}' {detail}".strip())

    # 4. Is the proxy alive? The walled passes need browser AND proxy together;
    #    with a dead proxy they exit 0 having recovered nothing from any walled
    #    host, which is indistinguishable from those hosts simply being blocked.
    proxy_ok, proxy_detail = proxy_health()
    print(f"proxy             = {proxy_ok} ({proxy_detail})", flush=True)
    if proxy_ok is False:
        problems.append(
            f"residential proxy is DOWN — {proxy_detail}; "
            f"every walled-host pass is running blind"
        )

    gained = "" if prev_n is None else f" ({now - prev_n:+,} since last check)"
    body = (
        f"<b>bulletin recovery watchdog</b>\n"
        f"churches with a PDF: {now:,}{gained}\n"
        f"still to recover: {gap:,}\n"
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
