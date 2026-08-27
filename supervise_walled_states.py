"""Keep per-state walled passes alive, from a cron rather than from a session.

The walled pass runs at the 2GB memory ceiling by construction -- a headless
browser per walled host alongside spaCy and pdfplumber -- so it dies
periodically. That is fine and expected: each church is CLAIMED before
processing and the queue is ordered least-recently-checked-first, so a relaunch
resumes at the frontier rather than redoing work.

What is NOT fine is that the thing doing the relaunching used to be a bash loop
inside an interactive Claude session. When the session ended, the supervisor
ended. On 2026-08-21 eight states died that way and sat idle for two days while
every Render service showed green; on 2026-08-23, minutes after that was
"fixed", the replacement loop was killed and VT died with nothing watching it.
A supervisor that lives in a session is not a supervisor.

So this is a cron. It holds no state file, which also removes the other historic
bug: several armed monitors ran the old script concurrently and raced on the
tracker, and a cycle that read the list before another rewrote it wrote back a
SHORTER list -- silently dropping eight states, including NY, CA and TX, from
being watched at all. Here the truth lives in two places that cannot be raced:
Render knows which jobs are running, and db99 knows which states still have a
gap. Nothing is remembered between runs.

Usage (Render cron):
    python -u supervise_walled_states.py
    python -u supervise_walled_states.py --dry-run
    python -u supervise_walled_states.py --max-running 6
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The service one-off walled passes run on.
RUNNER_SERVICE = os.environ.get("WALLED_RUNNER_SERVICE", "crn-d6s8t02a214c73bt62s0")
# Each pass is its own container at the plan's memory, so this caps spend and
# politeness to the hosts, not RAM.
MAX_RUNNING = int(os.environ.get("WALLED_MAX_RUNNING", "8"))
WALLED_BUDGET = int(os.environ.get("WALLED_BUDGET", "600"))
# Match the weekly cadence the bulletin cron runs on. These passes used
# --days-fresh 0, which re-crawls every church in a state on every relaunch --
# and since the supervisor relaunches hourly, that quietly put the walled
# recovery on a continuous churn no matter what the weekly cron said. A parish
# publishes ONE bulletin a week; crawling it hourly finds nothing and spends
# the residential proxy and the host's patience to find it.
#
# 6 days, matching SWEEP_DAYS_FRESH, so a relaunch still resumes at the
# frontier (churches are claim-stamped before processing) but stops re-walking
# ground covered this week.
WALLED_DAYS_FRESH = int(os.environ.get("SWEEP_DAYS_FRESH", "6"))
# Below this a state is not worth a dedicated 90-minute pass; the twice-daily
# sweep will reach it on its weekday turn.
MIN_GAP = int(os.environ.get("WALLED_MIN_GAP", "25"))

STATE_RE = re.compile(r"--state\s+([A-Z]{2})")


def _render(method, path, **kw):
    import requests

    key = os.environ.get("RENDER_API_KEY", "")
    r = requests.request(
        method,
        f"https://api.render.com/v1{path}",
        headers={"Authorization": f"Bearer {key}"},
        timeout=40,
        **kw,
    )
    r.raise_for_status()
    return r.json()


def busy_states():
    """States that already have a walled pass in flight.

    Read from Render, not from a file. A state being processed twice at once is
    the one genuinely dangerous outcome here: bulletin_pdf has no unique index,
    and the per-church claim is a select-then-stamp rather than SELECT ... FOR
    UPDATE, so two concurrent passes over one state can duplicate rows.
    """
    out = set()
    try:
        jobs = _render("GET", f"/services/{RUNNER_SERVICE}/jobs?limit=100")
    except Exception as e:
        # Unknown is NOT idle. An earlier version treated an API blip as "no
        # jobs running" and relaunched all thirteen states at once, which is
        # exactly the duplicate-row scenario above. Returning None makes the
        # caller stand down for this cycle.
        print(f"[ERR] cannot list jobs: {e}", flush=True)
        return None
    for e in jobs:
        j = e.get("job", e)
        if j.get("status") not in ("running", "pending"):
            continue
        m = STATE_RE.search(j.get("startCommand") or "")
        if m:
            out.add(m.group(1))
    return out


def states_with_gap():
    """[(state, gap)] worst first -- churches with a source but no PDF."""
    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.state_code AS st,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM bulletin_source bs
                                     WHERE bs.church_id = c.church_id)
                        THEN 1 ELSE 0 END)
             - SUM(CASE WHEN EXISTS (SELECT 1 FROM bulletin_source bs
                                     JOIN bulletin_pdf bp
                                       ON bp.bulletin_source_id = bs.bulletin_source_id
                                     WHERE bs.church_id = c.church_id)
                        THEN 1 ELSE 0 END) AS gap
        FROM church c
        WHERE c.website_url IS NOT NULL AND c.website_url <> ''
        GROUP BY c.state_code
        HAVING gap > 0
        ORDER BY gap DESC
        """
    )
    rows = [(r["st"], int(r["gap"])) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def launch(state):
    cmd = (
        f"python -u extract_bulletins_to_db99.py --state {state} "
        f"--walled-browser --walled-budget {WALLED_BUDGET} "
        f"--workers 1 --days-fresh {WALLED_DAYS_FRESH}"
    )
    d = _render("POST", f"/services/{RUNNER_SERVICE}/jobs", json={"startCommand": cmd})
    return d.get("id", "ERR")


def telegram(text):
    import requests

    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        return r.ok
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-running", type=int, default=MAX_RUNNING)
    ap.add_argument("--quiet", action="store_true",
                    help="Never send Telegram, even when launching")
    args = ap.parse_args()

    busy = busy_states()
    if busy is None:
        print("[--] standing down this cycle; will look again next hour",
              flush=True)
        return 0

    gaps = states_with_gap()
    print(f"in flight: {sorted(busy) or 'none'}  ({len(busy)}/{args.max_running})",
          flush=True)
    print(f"states with a gap: {len(gaps)}  worst: "
          f"{', '.join(f'{s}={g}' for s, g in gaps[:6])}", flush=True)

    slots = args.max_running - len(busy)
    if slots <= 0:
        print("[OK] all slots full; nothing to do", flush=True)
        return 0

    launched = []
    for st, gap in gaps:
        if slots <= 0:
            break
        if st in busy or gap < MIN_GAP:
            continue
        if args.dry_run:
            print(f"  [DRY] would launch {st} (gap {gap})", flush=True)
        else:
            jid = launch(st)
            print(f"  [GO]  {st} (gap {gap}) -> {jid}", flush=True)
        launched.append((st, gap))
        slots -= 1

    if launched and not args.quiet and not args.dry_run:
        telegram(
            "<b>walled supervisor</b>\nlaunched "
            + ", ".join(f"{s} (gap {g})" for s, g in launched)
            + f"\nin flight: {len(busy) + len(launched)}/{args.max_running}"
        )
    if not launched:
        print("[OK] nothing to launch", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
