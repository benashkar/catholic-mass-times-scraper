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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from src.utils import host_policy
from src.utils.db_connection import get_connection

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def probe(url, proxies=None, timeout=25):
    """Status code (or exception name) for one fetch.

    Streams and reads only the first chunk: we need the status, not the page.
    That matters on the proxy leg, which is billed by the gigabyte — pulling
    full pages for thousands of hosts would be paying to discard the body.
    """
    try:
        with requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=timeout,
            proxies=proxies,
            stream=True,
        ) as r:
            next(r.iter_content(2048), b"")
            return r.status_code
    except Exception as e:
        return type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--only-unlabelled", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=16,
                    help="Concurrent host probes; each host is hit once, so this "
                         "does not concentrate load on any single server")
    args = ap.parse_args()

    proxy = os.environ.get("PROXY_URL", "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    # PREFLIGHT: prove the proxy itself works before believing anything measured
    # through it. On 2026-08-07 the 711 plan expired mid-survey and every proxied
    # fetch came back 407; 690 hosts were recorded as "blocked" when the only
    # thing that had failed was our own account. A dead proxy must abort the run,
    # not quietly relabel the estate.
    if proxies:
        try:
            probe_code, _ = None, None
            r = requests.get(
                "http://example.com", headers={"User-Agent": UA},
                timeout=30, proxies=proxies,
            )
            probe_code = r.status_code
        except Exception as e:
            probe_code = type(e).__name__
        if probe_code != 200:
            print(
                f"[ABORT] proxy preflight failed: http://example.com -> {probe_code}.\n"
                "        A 407 means our account is out of traffic or unauthenticated.\n"
                "        Refusing to record verdicts that would blame the hosts."
            )
            return 2
        print("  [OK] proxy preflight passed (200)")

    conn = get_connection()
    conn.autocommit(True)
    cur = conn.cursor()
    host_policy.ensure_schema(cur)

    # One church per host — the policy is a property of the host, not the parish.
    # NOTE the doubled %% in the LIKE patterns: this statement is executed WITH
    # args, so pymysql runs it through % formatting first and a bare 'http%'
    # raises "unsupported format character".
    sql = """
        SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(website_url, '/', 3), '/', -1) AS host,
               MIN(website_url) AS url
        FROM church
        WHERE website_url LIKE 'http%%'
          AND website_url NOT LIKE '%%facebook.com%%'
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

    # church.website_url contains malformed entries ("http:// ic-brw@cdob.org",
    # embedded tabs, bare paths). Labelling those would fill the table with
    # hosts that cannot be fetched by anyone, so skip anything that is not a
    # plausible hostname rather than recording a meaningless verdict.
    def looks_like_host(h):
        return bool(
            h
            and "." in h
            and " " not in h
            and "\t" not in h
            and "@" not in h
            and not h.endswith(":")
            and len(h) < 200
        )

    targets = [(r["host"], r["url"]) for r in rows if looks_like_host(r["host"])]
    skipped = len(rows) - len(targets)

    tally = {"direct": 0, "needs_proxy": 0, "blocked": 0}
    lock = threading.Lock()
    done = {"n": 0}

    def work(item):
        host, url = item
        d = probe(url)
        # Only pay for a proxy probe when the direct fetch actually failed —
        # the proxy leg is metered, and a host that already works needs nothing.
        p = None
        if d != 200 and proxies:
            # Retry a proxy-side failure: the pool rotates, so a ProxyError means
            # we drew a bad exit node, NOT that the host refused us. Without this
            # 424 of 690 re-probes came back inconclusive and had to be redone.
            # A real HTTP status (even 403) is an answer and is kept immediately.
            for attempt in range(3):
                p = probe(url, proxies=proxies, timeout=40)
                if isinstance(p, int):
                    break
                if attempt < 2:
                    time.sleep(1.5)
        verdict = host_policy.classify(d, p)

        with lock:
            tally[verdict] += 1
            done["n"] += 1
            n = done["n"]
            if not args.dry_run:
                try:
                    host_policy.set_policy(
                        cur, host, verdict, str(d), str(p), egress, note="auto-probe"
                    )
                except Exception as e:
                    print(f"  [ERR] write {host}: {str(e)[:70]}", flush=True)
            if n % 100 == 0 or args.dry_run:
                print(
                    f"  [{n}/{len(targets)}] {host} direct={d} proxy={p} -> {verdict}",
                    flush=True,
                )

    # Each host is probed once, so concurrency here does not hammer any single
    # server; the work is almost entirely waiting on the network.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, targets))

    print(f"\negress={egress}  hosts={len(rows)}  skipped_malformed={skipped}")
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
