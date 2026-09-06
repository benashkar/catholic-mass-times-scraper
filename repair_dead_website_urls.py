"""
repair_dead_website_urls.py — fix church.website_url values that no longer resolve.

The host-policy survey found 436 hosts failing for non-block reasons
(ConnectionError / 404 / SSLError), affecting 869 churches. Sampling showed
~48% recover from a trivial variant: the stored URL is `http://` when only
`https://` answers, or it carries a `www.` the site dropped (or vice versa).
Those churches are silently unscrapeable for a reason that has nothing to do
with blocking.

Only rewrites within the SAME registrable domain — an `https://` or `www.`
variant of what is already stored. It will not point a parish at some other
site it happened to find, so a bad rewrite cannot silently attribute one
parish's bulletins to another.

Run as a Render one-off job: a URL's reachability must be judged from where the
scraper runs.

    python -u repair_dead_website_urls.py --dry-run
    python -u repair_dead_website_urls.py --commit --limit 500
"""

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from src.utils import host_policy
from src.utils.db_connection import get_connection

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Failure modes worth retrying with a variant. A 403 is deliberate refusal —
# a different spelling of the same host will not change that answer.
DEAD_STATUSES = ("ConnectionError", "ConnectTimeout", "SSLError", "404", "500", "522")


def try_url(url, timeout=12):
    try:
        with requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        ) as r:
            next(r.iter_content(512), b"")
            return r.status_code, r.url
    except Exception:
        return None, None


def variants(host):
    bare = host[4:] if host.startswith("www.") else host
    # Ordered by how likely they are to be the site's canonical form.
    return list(
        dict.fromkeys(
            [
                f"https://{host}",
                f"https://www.{bare}",
                f"https://{bare}",
                f"http://www.{bare}",
            ]
        )
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    write = args.commit and not args.dry_run

    conn = get_connection()
    conn.autocommit(True)
    cur = conn.cursor()

    fmt = ",".join(["%s"] * len(DEAD_STATUSES))
    cur.execute(
        f"""
        SELECT host, direct_status FROM scrape_host_policy
        WHERE policy = 'blocked' AND direct_status IN ({fmt})
        ORDER BY host LIMIT %s
        """,
        (*DEAD_STATUSES, args.limit),
    )
    hosts = cur.fetchall()
    print(f"dead hosts to try: {len(hosts):,}   mode: {'COMMIT' if write else 'DRY-RUN'}")

    lock = threading.Lock()
    stats = {"fixed": 0, "still_dead": 0, "churches": 0}

    def work(row):
        host = row["host"]
        good = None
        for v in variants(host):
            code, final = try_url(v)
            if code == 200:
                good = final or v
                break

        with lock:
            if not good:
                stats["still_dead"] += 1
                return
            stats["fixed"] += 1
            if not write:
                print(f"  [DRY] {host} -> {good}")
                return
            # Repoint only the churches on this exact broken host.
            cur.execute(
                """
                UPDATE church
                SET website_url = %s
                WHERE SUBSTRING_INDEX(SUBSTRING_INDEX(website_url, '/', 3), '/', -1) = %s
                """,
                (good[:500], host),
            )
            stats["churches"] += cur.rowcount
            # The host answers now, so its policy verdict is stale.
            host_policy.set_policy(
                cur,
                host_policy.host_of(good),
                "direct",
                "200",
                None,
                "url-repair",
                note=f"repaired from {host}",
            )
            print(f"  [OK] {host} -> {good}  ({cur.rowcount} churches)")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, hosts))

    print(
        f"\nfixed={stats['fixed']:,}  still_dead={stats['still_dead']:,}"
        f"  churches_repointed={stats['churches']:,}"
    )
    if not write:
        print("DRY-RUN — nothing written.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
