"""Report Wisconsin bulletin coverage from db99 back through S3.

db99 is unreachable from the workstation (it sits behind a VPC endpoint), and
a Render one-off job's stdout cannot be read back — GET /v1/logs returns
logs: null once it exits. So the only way to see what a run actually did is to
have the job itself query db99 and publish the answer somewhere readable.

Publishes a marker line FIRST, before touching the database, so that a missing
readback distinguishes "the job never got that far" from "the query failed".

Usage:
    python scripts/verify_wi_bulletins.py
    python scripts/verify_wi_bulletins.py --state WI
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402
from src.utils.telegram import send_telegram  # noqa: E402

def _target_ids():
    """The churches this recovery is about — read from the seed file itself.

    This used to be a hand-copied slice of 18 ids, which quietly reported 0
    recovered while 30 churches had in fact just gained their first PDF: the
    sample simply missed all of them. Read the real list so the report cannot
    disagree with what was actually worked on.
    """
    import json

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds",
                        "wi_verified_bulletin_pages.json")
    try:
        return sorted({int(r["church_id"]) for r in json.load(open(path, encoding="utf-8"))})
    except Exception:
        return []


ZERO_NAME_IDS = _target_ids()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="WI")
    args = ap.parse_args()
    state = args.state.upper()

    lines = [f"verify_bulletins start state={state}"]
    publish("verify_wi_bulletins", "\n".join(lines) + "\n(marker: job reached the script)")

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()

    def scalar(sql, params=None):
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return list(row.values())[0] if isinstance(row, dict) else row[0]

    lines.append("")
    lines.append(f"--- {state} totals ---")
    lines.append(f"churches                 {scalar('SELECT COUNT(*) FROM church WHERE state_code=%s', (state,))}")
    lines.append(f"with website_url         {scalar('SELECT COUNT(*) FROM church WHERE state_code=%s AND website_url IS NOT NULL AND website_url<>%s', (state, ''))}")
    lines.append(f"with bulletin_source     {scalar('SELECT COUNT(DISTINCT bs.church_id) FROM bulletin_source bs JOIN church c ON c.church_id=bs.church_id WHERE c.state_code=%s', (state,))}")
    lines.append(f"with >=1 bulletin_pdf    {scalar('SELECT COUNT(DISTINCT bs.church_id) FROM bulletin_source bs JOIN bulletin_pdf bp ON bp.bulletin_source_id=bs.bulletin_source_id JOIN church c ON c.church_id=bs.church_id WHERE c.state_code=%s', (state,))}")
    # Captured here, while the cursor is still open, because the exit-status
    # check at the bottom runs after the connection is closed.
    total_pdfs = int(scalar('SELECT COUNT(*) FROM bulletin_pdf bp JOIN bulletin_source bs ON bs.bulletin_source_id=bp.bulletin_source_id JOIN church c ON c.church_id=bs.church_id WHERE c.state_code=%s', (state,)))
    lines.append(f"total bulletin_pdf rows  {total_pdfs}")
    lines.append(f"pdfs text_extracted      {scalar('SELECT COUNT(*) FROM bulletin_pdf bp JOIN bulletin_source bs ON bs.bulletin_source_id=bp.bulletin_source_id JOIN church c ON c.church_id=bs.church_id WHERE c.state_code=%s AND bp.text_extracted=1', (state,))}")
    lines.append(f"bulletin_name rows       {scalar('SELECT COUNT(*) FROM bulletin_name bn JOIN bulletin_pdf bp ON bp.bulletin_pdf_id=bn.bulletin_pdf_id JOIN bulletin_source bs ON bs.bulletin_source_id=bp.bulletin_source_id JOIN church c ON c.church_id=bs.church_id WHERE c.state_code=%s', (state,))}")

    lines.append("")
    lines.append("--- newest bulletin editions ---")
    cur.execute(
        "SELECT bp.pdf_date, COUNT(*) n FROM bulletin_pdf bp "
        "JOIN bulletin_source bs ON bs.bulletin_source_id=bp.bulletin_source_id "
        "JOIN church c ON c.church_id=bs.church_id "
        "WHERE c.state_code=%s AND bp.pdf_date IS NOT NULL "
        "GROUP BY bp.pdf_date ORDER BY bp.pdf_date DESC LIMIT 8",
        (state,),
    )
    for r in cur.fetchall():
        lines.append(f"  {r['pdf_date']}  {r['n']}")

    lines.append("")
    lines.append(f"--- the {len(ZERO_NAME_IDS)} recovery targets ---")
    fmt = ",".join(["%s"] * len(ZERO_NAME_IDS))
    cur.execute(
        f"SELECT c.church_id, c.name, c.website_url, "
        f"  (SELECT bs.bulletin_page_url FROM bulletin_source bs "
        f"   WHERE bs.church_id=c.church_id LIMIT 1) page, "
        f"  (SELECT COUNT(*) FROM bulletin_pdf bp JOIN bulletin_source bs "
        f"     ON bs.bulletin_source_id=bp.bulletin_source_id "
        f"   WHERE bs.church_id=c.church_id) pdfs, "
        f"  (SELECT COUNT(*) FROM bulletin_name bn JOIN bulletin_pdf bp "
        f"     ON bp.bulletin_pdf_id=bn.bulletin_pdf_id JOIN bulletin_source bs "
        f"     ON bs.bulletin_source_id=bp.bulletin_source_id "
        f"   WHERE bs.church_id=c.church_id) names "
        f"FROM church c WHERE c.church_id IN ({fmt}) ORDER BY c.church_id",
        ZERO_NAME_IDS,
    )
    rows = cur.fetchall()
    got = [r for r in rows if r["pdfs"]]
    still = [r for r in rows if not r["pdfs"]]
    lines.append(f"RECOVERED {len(got)} / {len(rows)}   still-zero {len(still)}")
    lines.append("")
    lines.append("  -- recovered --")
    for r in got:
        lines.append(f"  {r['church_id']:6} {(r['name'] or '')[:30]:32} pdfs={r['pdfs']:4} names={r['names']}")
    lines.append("")
    lines.append("  -- still zero (website_url | stored bulletin page) --")
    for r in still:
        lines.append(f"  {r['church_id']:6} {(r['name'] or '')[:26]:28} {(r['website_url'] or '(none)')[:38]:40} {(r['page'] or '(none)')[:44]}")

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)

    # S3 publish is best-effort. This service's IAM user carries Secrets
    # Manager access for db99 but not s3:PutObject on the diagnostics bucket,
    # so publish() no-ops here — the first verify job reported success while
    # writing nothing at all. Telegram is the channel that works from here.
    publish("verify_wi_bulletins", out)
    send_telegram("<pre>" + out[:3800] + "</pre>")

    # A one-off job's stdout is unreadable, but failed-vs-succeeded does
    # survive back to the caller. Make that one bit carry what matters.
    #
    # This used to re-query here, after cur.close()/conn.close() a few lines
    # up, and died with "Cursor closed" every time — which read from outside
    # as "the state has no PDFs" when the counts had in fact been gathered and
    # sent successfully. Use the value captured while the cursor was open.
    if total_pdfs == 0:
        raise SystemExit("NO bulletin_pdf rows for " + state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = "verify_wi_bulletins FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("verify_wi_bulletins", tb)
        sys.exit(1)
