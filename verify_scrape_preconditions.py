"""
verify_scrape_preconditions.py — check the things that must be TRUE for a weekly
run to be meaningful, BEFORE and AFTER it runs.

Every silent failure this project has had shared one shape: the run completed,
the exit code was green, rows even landed — and the result was still wrong.
Checking "did it finish?" never catches that. These checks assert the
preconditions instead, each one earned from a real incident:

  1. PROXY REACHABLE — 2026-08-07: the 711 plan expired mid-run, every proxied
     fetch returned 407, and 690 hosts were recorded as "blocked" when the only
     thing that failed was our own account. A dead proxy silently rewrites the
     map of the internet.
  2. needs_proxy HOSTS ACTUALLY YIELD — 342 churches sit on hosts that only
     answer through the proxy. They were swept while the proxy was dead, so
     their results were meaningless and had to be redone. If this cohort stops
     producing bulletin pages, the proxy is off or broken again.
  3. HOST POLICY NOT STALE — labels drive whether we proxy a host at all. Once
     they age, we are routing on last month's reality.
  4. ROTATION ADVANCING — the original bug: the sweep restarted at Alaska every
     week for five months. A frontier that stops moving means no forward
     progress, regardless of how many names land.
  5. LABEL SANITY — if 'needs_proxy' collapses to zero, per-host routing has
     silently become "never proxy", which looks fine until coverage rots.

Exit code is non-zero if any check fails, so it can gate a pipeline.

    python verify_scrape_preconditions.py
    python verify_scrape_preconditions.py --no-telegram
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from src.utils.db_connection import get_connection
from src.utils.telegram import send_telegram

POLICY_MAX_AGE_DAYS = 45
FRONTIER_MAX_AGE_DAYS = 21


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    # accepted for compatibility with verify_all.py, which passes --days
    ap.add_argument("--days", type=int, default=7, help=argparse.SUPPRESS)
    args = ap.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    results = []  # (name, ok, detail)

    def check(name, ok, detail):
        results.append((name, bool(ok), detail))

    # 1. Proxy reachable — only meaningful if we are configured to use one.
    proxy = os.environ.get("PROXY_URL", "").strip() or None
    if proxy:
        try:
            r = requests.get(
                "http://example.com", timeout=30,
                proxies={"http": proxy, "https": proxy},
            )
            code = r.status_code
        except Exception as e:
            code = type(e).__name__
        check(
            "proxy reachable", code == 200,
            f"http://example.com via PROXY_URL -> {code}"
            + ("  (407 = account out of traffic / unauthenticated)" if code == 407 else ""),
        )
    else:
        check("proxy reachable", True, "PROXY_URL unset — no proxied hosts expected")

    # 2. needs_proxy cohort is still producing bulletin pages.
    cur.execute(
        """
        SELECT COUNT(*) churches,
               SUM(EXISTS(SELECT 1 FROM bulletin_source bs
                          WHERE bs.church_id = c.church_id
                            AND bs.discovery_source <> 'not_found')) with_page
        FROM church c
        JOIN scrape_host_policy p
          ON p.host = SUBSTRING_INDEX(SUBSTRING_INDEX(c.website_url, '/', 3), '/', -1)
        WHERE p.policy = 'needs_proxy'
        """
    )
    r = cur.fetchone()
    total, with_page = r["churches"] or 0, int(r["with_page"] or 0)
    pct = (with_page / total * 100) if total else 0
    # These hosts answer ONLY through the proxy, so a healthy run should find a
    # page for a decent share of them. Near-zero means we scraped them direct.
    check(
        "needs_proxy hosts yielding", total == 0 or pct >= 25,
        f"{with_page:,}/{total:,} needs_proxy churches have a bulletin page ({pct:.0f}%)",
    )

    # 3. Host policy freshness.
    cur.execute("SELECT MAX(checked_at) v, COUNT(*) n FROM scrape_host_policy")
    r = cur.fetchone()
    cur.execute(
        "SELECT DATEDIFF(NOW(), MAX(checked_at)) d FROM scrape_host_policy"
    )
    age = cur.fetchone()["d"]
    check(
        "host policy fresh", age is not None and age <= POLICY_MAX_AGE_DAYS,
        f"{r['n']:,} hosts labelled, newest {age} days old "
        f"(limit {POLICY_MAX_AGE_DAYS}) — re-run label_host_proxy_policy.py",
    )

    # 4. Rotation frontier advancing: the OLDEST due church should not be
    #    ancient, or we are stuck the way we were for five months.
    #    This MUST mirror the sweep's own exclusion filter. Without it the check
    #    counts churches the sweep deliberately never touches — diocese
    #    directory pages and Facebook links — and alerts forever about a
    #    "stuck" rotation that is working exactly as designed. (Measured: all 63
    #    apparently-stale churches were excluded ones; zero were real.)
    cur.execute(
        """
        SELECT DATEDIFF(NOW(), MIN(bulletin_checked_at)) d
        FROM church c
        WHERE website_url LIKE 'http%%'
          AND website_url NOT LIKE '%%facebook.com%%'
          AND website_url NOT LIKE '%%diocese%%'
          AND website_url NOT LIKE '%%archdiocese%%'
          AND EXISTS (SELECT 1 FROM bulletin_source bs WHERE bs.church_id = c.church_id)
        """
    )
    frontier = cur.fetchone()["d"]
    check(
        "rotation advancing", frontier is not None and frontier <= FRONTIER_MAX_AGE_DAYS,
        f"oldest checked church is {frontier} days old (limit {FRONTIER_MAX_AGE_DAYS})",
    )

    # 5. Label sanity.
    cur.execute("SELECT policy, COUNT(*) n FROM scrape_host_policy GROUP BY policy")
    counts = {row["policy"]: row["n"] for row in cur.fetchall()}
    check(
        "policy labels sane", counts.get("needs_proxy", 0) > 0 and counts.get("direct", 0) > 0,
        f"direct={counts.get('direct',0):,} blocked={counts.get('blocked',0):,} "
        f"needs_proxy={counts.get('needs_proxy',0):,}",
    )

    failed = [n for n, ok, _ in results if not ok]
    ok_all = not failed

    lines = [f"{'[OK]' if ok_all else '[ALERT]'} <b>Scrape preconditions</b>", ""]
    for name, ok, detail in results:
        lines.append(f"{'[OK]  ' if ok else '[FAIL]'} {name}: {detail}")
    if failed:
        lines.append("")
        lines.append(f"failing: {', '.join(failed)}")

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", ""))
    if not args.no_telegram:
        send_telegram(msg)

    conn.close()
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
