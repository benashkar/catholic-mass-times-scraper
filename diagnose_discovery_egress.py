"""
diagnose_discovery_egress.py — does bulletin discovery fail from Render's IP?

1,441 sources were marked not_found during the recovery, yet spot-checking them
from a residential connection shows discovery working in ~2.5s and finding 100
PDFs. That points at Render's datacenter egress being blocked or throttled by
those parish hosts rather than at the sites being down.

Render does not serve cron runtime logs, so results are written to scrape_log
where they can be read back with SQL.

Usage (as a Render one-off job):
    python -u diagnose_discovery_egress.py --limit 25
"""

import argparse
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from run_bulletin_scraper import find_bulletin_page
from src.utils.db_connection import get_connection

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT ch.church_id, ch.website_url,
               (SELECT COUNT(*) FROM bulletin_pdf bp
                 WHERE bp.bulletin_source_id = bs.bulletin_source_id) pdfs
        FROM bulletin_source bs
        JOIN church ch ON ch.church_id = bs.church_id
        WHERE bs.discovery_source = 'not_found'
          AND EXISTS (SELECT 1 FROM bulletin_pdf bp
                      WHERE bp.bulletin_source_id = bs.bulletin_source_id)
        ORDER BY pdfs DESC
        LIMIT %s
        """,
        (args.limit,),
    )
    rows = cur.fetchall()

    try:
        egress_ip = requests.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception as e:
        egress_ip = f"unknown ({type(e).__name__})"

    results = {"ok": 0, "no_page": 0, "http_err": 0, "conn_err": 0}
    detail = []
    for r in rows:
        url = r["website_url"] or ""
        if not url.startswith("http"):
            detail.append(f"{r['church_id']}:bad_url")
            results["conn_err"] += 1
            continue
        # Raw reachability first — separates "host refuses us" from "no bulletin
        # page found", which look identical in the pipeline's output.
        code = None
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            code = resp.status_code
        except Exception as e:
            code = type(e).__name__
            results["conn_err"] += 1

        found = None
        t0 = time.time()
        try:
            res = find_bulletin_page(url)
            found = res.get("source")
        except Exception as e:
            found = f"EXC:{type(e).__name__}"
        took = time.time() - t0

        if found and found != "not_found":
            results["ok"] += 1
        elif isinstance(code, int) and code == 200:
            results["no_page"] += 1
        elif isinstance(code, int):
            results["http_err"] += 1

        detail.append(f"{r['church_id']}|http={code}|disc={found}|{took:.1f}s")

    notes = json.dumps(
        {
            "egress_ip": egress_ip,
            "host": socket.gethostname(),
            "tested": len(rows),
            "summary": results,
            "detail": detail[:20],
        }
    )[:4000]

    cur.execute(
        """
        INSERT INTO scrape_log (scrape_type, completed_at, status,
            communities_scraped, churches_scraped, services_upserted, errors, notes)
        VALUES (%s, NOW(), %s, 0, %s, 0, NULL, %s)
        """,
        ("discovery_egress_probe", "completed", len(rows), notes),
    )
    conn.commit()
    print(notes)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
