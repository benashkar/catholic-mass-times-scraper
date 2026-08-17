"""Seed hand-verified bulletin pages into bulletin_source on db99.

Why this exists
---------------
Discovery starts from church.website_url every run. That is fine until the
stored site is wrong — and for small parishes it very often is: domains lapse
(stlouisparishwi.com now 403-redirects to an unrelated business), parishes
merge and publish under a cluster identity (St. Columbkille's bulletin lives
under St. Katharine Drexel, St. Joan of Arc's under scsjcluster.org), or the
stored value was never a parish site at all (a catholicmasses.org aggregator
page, or the literal string "#").

No amount of better parsing recovers those, because the input URL is wrong.
This script records the bulletin page we verified by hand, and
process_church() falls back to it when discovery from website_url comes up
empty.

Each page in the seed file was confirmed to serve a real bulletin PDF
(HTTP 200/206 plus %PDF magic bytes — the ParishesOnline CDN mislabels its
Content-Type as application/octet-stream, so headers alone are not enough).

Usage:
    python scripts/seed_verified_bulletin_pages.py --dry-run
    python scripts/seed_verified_bulletin_pages.py
    python scripts/seed_verified_bulletin_pages.py --file <path.json>
"""

import argparse
import contextlib
import io
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extract_bulletins_to_db99 import get_connection  # noqa: E402
from scripts._readback import publish  # noqa: E402

DEFAULT_SEED = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "seeds", "wi_verified_bulletin_pages.json"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_SEED, help="Seed JSON path")
    ap.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    args = ap.parse_args()

    rows = json.load(open(args.file, encoding="utf-8"))
    print(f"Loaded {len(rows)} verified bulletin pages from {args.file}")

    conn = get_connection()
    cur = conn.cursor()

    inserted = updated = unchanged = missing = 0
    for r in rows:
        church_id = r["church_id"]
        page = (r.get("bulletin_page_url") or "").strip()
        if not page:
            continue

        cur.execute("SELECT church_id FROM church WHERE church_id = %s", (church_id,))
        if not cur.fetchone():
            print(f"  [WARN] church_id {church_id} ({r.get('name')}) not in church table")
            missing += 1
            continue

        cur.execute(
            "SELECT bulletin_source_id, bulletin_page_url FROM bulletin_source "
            "WHERE church_id = %s LIMIT 1",
            (church_id,),
        )
        existing = cur.fetchone()

        if existing is None:
            action, inserted = "INSERT", inserted + 1
        elif (existing.get("bulletin_page_url") or "") == page:
            unchanged += 1
            continue
        else:
            action, updated = "UPDATE", updated + 1

        print(f"  [{action}] {church_id} {(r.get('name') or '')[:34]:36} -> {page}")
        if args.dry_run:
            continue

        if action == "INSERT":
            cur.execute(
                "INSERT INTO bulletin_source "
                "(church_id, discovery_source, bulletin_page_url, discovered_at) "
                "VALUES (%s, %s, %s, NOW())",
                (church_id, "verified_probe", page[:2048]),
            )
        else:
            cur.execute(
                "UPDATE bulletin_source SET bulletin_page_url = %s, "
                "discovery_source = %s, discovered_at = NOW() WHERE bulletin_source_id = %s",
                (page[:2048], "verified_probe", existing["bulletin_source_id"]),
            )

    print(
        f"\n{'DRY RUN — ' if args.dry_run else ''}"
        f"inserted={inserted} updated={updated} unchanged={unchanged} missing={missing}"
    )
    cur.close()
    conn.close()


if __name__ == "__main__":
    # A Render one-off job's stdout cannot be read back — /v1/logs returns
    # logs: null once it exits — so capture everything, including a traceback,
    # and publish it to S3 where it can actually be retrieved.
    buf = io.StringIO()
    failed = False
    try:
        with contextlib.redirect_stdout(buf):
            main()
    except Exception:
        failed = True
        buf.write("\n[ERR] seed failed:\n" + traceback.format_exc())

    out = buf.getvalue()
    print(out, flush=True)
    publish("seed_verified_bulletin_pages", out)
    sys.exit(1 if failed else 0)
