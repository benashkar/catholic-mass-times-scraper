"""Why does the residual gap produce nothing? Probe a real sample and count.

The gap is 9,530 churches that have a bulletin_source row and no PDF, and
6,285 of them carry discovery_source='not_found' -- a placeholder written when
discovery came up empty. 9,387 of the 9,530 were checked within the last seven
days, so re-running the same passes over them is not going to change anything;
whatever is wrong is wrong every time.

The gap spans 5,236 distinct hosts, so there is no shared-vendor fix left of
the kind that took national coverage from 7,584 to 13,140 (the LPi JSON API).
What is left has to be characterised before it can be worked, because "no
bulletin" is the same observable for at least four completely different causes:

  DEAD        the website does not resolve or does not answer at all
  WALLED      it answers 403/503 to us specifically
  ALIVE       it answers 200 -- so the site is fine and DISCOVERY is what failed
  REDIRECTED  it answers, but somewhere else entirely (merged/renamed parish)

Only the third is a scraper bug. The first needs URL repair, the second needs
the browser+proxy path, the fourth needs the stored URL replaced. Guessing
which dominates is how two days got spent on the wrong fix earlier in this
project, so: sample, probe, count.

Read-only. Writes nothing.

Usage:
    python -u diag_gap_notfound.py --sample 150
    python -u diag_gap_notfound.py --sample 150 --state WI
"""

import argparse
import os
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROXY_URL = os.environ.get("PROXY_URL", "").strip() or None
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def gap_sample(n, state=None, only_notfound=True):
    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()
    where = [
        "c.website_url IS NOT NULL",
        "c.website_url <> ''",
        "EXISTS (SELECT 1 FROM bulletin_source bs WHERE bs.church_id=c.church_id)",
        """NOT EXISTS (SELECT 1 FROM bulletin_source bs JOIN bulletin_pdf bp
                       ON bp.bulletin_source_id=bs.bulletin_source_id
                       WHERE bs.church_id=c.church_id)""",
    ]
    params = []
    if state:
        where.append("c.state_code=%s")
        params.append(state)
    if only_notfound:
        where.append(
            """EXISTS (SELECT 1 FROM bulletin_source bs
                       WHERE bs.church_id=c.church_id
                         AND bs.discovery_source='not_found')"""
        )
    cur.execute(
        f"""SELECT c.church_id, c.name, c.state_code, c.website_url
            FROM church c WHERE {' AND '.join(where)}""",
        params,
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    random.seed(20260823)  # reproducible sample; re-runs compare like for like
    random.shuffle(rows)
    return rows[:n]


def probe(row):
    """Classify one church's website. Tries plain, then proxy, then both+browser
    is left to the real pipeline -- here we only need the dominant cause."""
    import requests

    url = row["website_url"]
    if not url.startswith("http"):
        url = "https://" + url
    out = {"church_id": row["church_id"], "state": row["state_code"],
           "name": row["name"], "url": url}

    def attempt(proxies, label):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Accept": "text/html,*/*"},
                timeout=25,
                allow_redirects=True,
                proxies=proxies,
                verify=False,
            )
            return r, label, None
        except Exception as e:
            return None, label, type(e).__name__

    r, how, err = attempt(None, "direct")
    if r is None or r.status_code in (403, 406, 429, 503):
        if PROXY_URL:
            r2, how2, err2 = attempt(
                {"http": PROXY_URL, "https": PROXY_URL}, "proxy"
            )
            if r2 is not None and r2.status_code < 400:
                r, how, err = r2, how2, err2

    if r is None:
        out["verdict"] = "DEAD"
        out["detail"] = err or "no response"
        return out

    out["status"] = r.status_code
    out["how"] = how
    final = r.url
    out["final"] = final
    if r.status_code in (403, 406, 429, 503):
        out["verdict"] = "WALLED"
        out["detail"] = f"{r.status_code} via {how}"
        return out
    if r.status_code >= 400:
        out["verdict"] = "DEAD"
        out["detail"] = f"HTTP {r.status_code}"
        return out

    body = (r.text or "")[:400000].lower()
    # Does the page even mention a bulletin? If it does and discovery still
    # found nothing, that is a scraper bug and the most actionable class here.
    words = ("bulletin", "newsletter", "worship folder", "order of worship",
             "weekly update", "announcements")
    hit = [w for w in words if w in body]
    out["verdict"] = "ALIVE_HAS_BULLETIN_WORD" if hit else "ALIVE_NO_BULLETIN_WORD"
    out["detail"] = ",".join(hit[:3]) if hit else f"{len(body)}b, no keyword"

    # Parked / for-sale / registrar placeholder pages answer 200 and look alive.
    for junk in ("domain is for sale", "buy this domain", "godaddy.com/forsale",
                 "this domain may be for sale", "website coming soon",
                 "under construction", "account suspended"):
        if junk in body:
            out["verdict"] = "PARKED"
            out["detail"] = junk
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--state", default=None)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--all-gap", action="store_true",
                    help="Sample the whole gap, not just discovery_source=not_found")
    args = ap.parse_args()

    import urllib3

    urllib3.disable_warnings()

    rows = gap_sample(args.sample, args.state, only_notfound=not args.all_gap)
    print(f"probing {len(rows)} churches "
          f"({'whole gap' if args.all_gap else 'not_found only'}"
          f"{', ' + args.state if args.state else ''})"
          f"  proxy={'yes' if PROXY_URL else 'NO'}", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(probe, rows), 1):
            results.append(res)
            if i % 25 == 0:
                print(f"  ...{i}/{len(rows)}", flush=True)

    counts = Counter(r["verdict"] for r in results)
    print("\n================ VERDICTS ================")
    for v, n in counts.most_common():
        print(f"  {v:26} {n:4}  {100.0*n/len(results):5.1f}%")

    print("\n--- samples per verdict ---")
    for v in counts:
        print(f"\n[{v}]")
        for r in [x for x in results if x["verdict"] == v][:4]:
            print(f"  {r['state']} {str(r['name'])[:38]:38} {str(r['url'])[:52]}")
            print(f"      -> {r.get('detail','')}")
            if r.get("final") and r["final"].rstrip("/") != r["url"].rstrip("/"):
                print(f"      -> redirected to {r['final'][:70]}")

    alive = counts["ALIVE_HAS_BULLETIN_WORD"]
    print("\n================ READING ================")
    print(f"sites that are UP and say 'bulletin' but yielded nothing: {alive}"
          f" ({100.0*alive/len(results):.0f}%)")
    print("That fraction of the gap is a DISCOVERY bug, not a dead parish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
