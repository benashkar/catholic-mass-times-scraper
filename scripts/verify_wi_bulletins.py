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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts._readback import publish  # noqa: E402

# The 69 Wisconsin churches that had zero extracted names going in.
ZERO_NAME_IDS = [
    28440, 28441, 28491, 28511, 28532, 28533, 28552, 28592, 28593, 28770,
    28863, 28864, 29036, 29039, 29048, 29058, 29146, 29222,
]


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
    lines.append(f"total bulletin_pdf rows  {scalar('SELECT COUNT(*) FROM bulletin_pdf bp JOIN bulletin_source bs ON bs.bulletin_source_id=bp.bulletin_source_id JOIN church c ON c.church_id=bs.church_id WHERE c.state_code=%s', (state,))}")
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
    lines.append("--- the churches that had zero names ---")
    fmt = ",".join(["%s"] * len(ZERO_NAME_IDS))
    cur.execute(
        f"SELECT c.church_id, c.name, "
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
    for r in cur.fetchall():
        lines.append(f"  {r['church_id']:6} {(r['name'] or '')[:34]:36} pdfs={r['pdfs']:4} names={r['names']}")

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)
    publish("verify_wi_bulletins", out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = "verify_wi_bulletins FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("verify_wi_bulletins", tb)
        sys.exit(1)
