"""
label_host_proxy_policy.py — measure each host and record direct / needs_proxy / blocked.

Must run WHERE THE SCRAPER RUNS (a Render one-off job). Measuring from a laptop
gives the wrong answer: on 2026-08-07 the same 7 parish sites returned 200
residentially and 403 from Render, and a laptop-based survey had certified them
as fine while they were failing in production.

Each host is fetched twice in the same run — direct, then through PROXY_URL —
so the verdict rests on a controlled comparison:

    direct 200                     -> direct        (no proxy; the default)
    direct fails, proxy 200        -> needs_proxy   (proxy earns its cost)
    both fail                      -> blocked       (a proxy would burn GB for the same 403)

Usage (Render one-off):
    python -u label_host_proxy_policy.py --limit 400
    python -u label_host_proxy_policy.py --limit 400 --only-unlabelled
"""

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from src.utils import host_policy
from src.utils.db_connection import get_connection

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def probe(url, proxies=None, timeout=25):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, proxies=proxies)
        return r.status_code
    except Exception as e:
        return type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--only-unlabelled", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proxy = os.environ.get("PROXY_URL", "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    conn = get_connection()
    conn.autocommit(True)
    cur = conn.cursor()
    host_policy.ensure_schema(cur)

    # One church per host — the policy is a property of the host, not the parish.
    sql = """
        SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(website_url, '/', 3), '/', -1) AS host,
               MIN(website_url) AS url
        FROM church
        WHERE website_url LIKE 'http%'
          AND website_url NOT LIKE '%facebook.com%'
        GROUP BY host
    """
    if args.only_unlabelled:
        sql = sql.replace(
            "GROUP BY host",
            "AND SUBSTRING_INDEX(SUBSTRING_INDEX(website_url,'/',3),'/',-1) "
            "NOT IN (SELECT host FROM scrape_host_policy) GROUP BY host",
        )
    sql += " ORDER BY host LIMIT %s"
    cur.execute(sql, (args.limit,))
    rows = cur.fetchall()

    try:
        egress = requests.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception:
        egress = socket.gethostname()

    tally = {"direct": 0, "needs_proxy": 0, "blocked": 0}
    for i, r in enumerate(rows, 1):
        host, url = r["host"], r["url"]
        if not host:
            continue
        d = probe(url)
        # Only pay for a proxy probe when the direct fetch actually failed.
        p = None
        if d != 200 and proxies:
            p = probe(url, proxies=proxies, timeout=40)

        verdict = host_policy.classify(d, p)
        tally[verdict] += 1
        if not args.dry_run:
            host_policy.set_policy(
                cur, host, verdict, str(d), str(p), egress,
                note="auto-probe",
            )
        if i % 25 == 0 or args.dry_run:
            print(f"  [{i}/{len(rows)}] {host} direct={d} proxy={p} -> {verdict}", flush=True)
        time.sleep(0.2)

    print(f"\negress={egress}  hosts={len(rows)}")
    print(f"  direct      : {tally['direct']:,}")
    print(f"  needs_proxy : {tally['needs_proxy']:,}")
    print(f"  blocked     : {tally['blocked']:,}")
    if args.dry_run:
        print("DRY-RUN — nothing written.")

    cur.execute(
        "INSERT INTO scrape_log (scrape_type, completed_at, status, communities_scraped,"
        " churches_scraped, services_upserted, errors, notes)"
        " VALUES (%s, NOW(), %s, 0, %s, 0, NULL, %s)",
        ("host_policy_labelling", "completed", len(rows),
         f"egress={egress} direct={tally['direct']} needs_proxy={tally['needs_proxy']}"
         f" blocked={tally['blocked']}"),
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
