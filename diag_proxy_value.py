"""How much would a working proxy actually buy us?

The shared 711 endpoint is returning 407 from Render, so the question before
spending is not "is a proxy nice to have" but "how many churches are actually
gated behind one". Answer it with numbers instead of an argument.

Reports, for the given state:
  1. the scrape_host_policy distribution as it stands
  2. churches with zero PDFs, grouped by their host's policy — the ones on
     needs_proxy hosts are exactly what a top-up unblocks
  3. how many zero-PDF churches sit on hosts nobody has measured yet, which is
     the population a labelling run would move out of the unknown column

Deliberately does NOT re-probe hosts: with the proxy 407ing, every verdict this
run recorded would blame the host for our own account lapsing. That mistake has
already been made once here, relabelling 690 hosts as blocked.

Usage (Render one-off):
    python -u diag_proxy_value.py --state WI
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="WI")
    args = ap.parse_args()
    state = args.state.upper()

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()

    lines = [f"proxy value analysis — state={state}", ""]

    cur.execute("SELECT policy, COUNT(*) n FROM scrape_host_policy GROUP BY policy ORDER BY n DESC")
    lines.append("--- scrape_host_policy, whole estate ---")
    for r in cur.fetchall():
        lines.append(f"  {r['policy']:14} {r['n']}")

    # Zero-PDF churches in this state, bucketed by their host's recorded policy.
    host_expr = "SUBSTRING_INDEX(SUBSTRING_INDEX(c.website_url, '/', 3), '/', -1)"
    cur.execute(
        f"""
        SELECT COALESCE(p.policy, '(unmeasured)') AS policy, COUNT(*) AS n
        FROM church c
        LEFT JOIN scrape_host_policy p ON p.host = {host_expr}
        WHERE c.state_code = %s
          AND c.website_url IS NOT NULL AND c.website_url <> ''
          AND NOT EXISTS (
                SELECT 1 FROM bulletin_source bs
                JOIN bulletin_pdf bp ON bp.bulletin_source_id = bs.bulletin_source_id
                WHERE bs.church_id = c.church_id)
        GROUP BY COALESCE(p.policy, '(unmeasured)')
        ORDER BY n DESC
        """,
        (state,),
    )
    lines.append("")
    lines.append(f"--- {state} churches with ZERO pdfs, by host policy ---")
    total = 0
    for r in cur.fetchall():
        lines.append(f"  {r['policy']:14} {r['n']}")
        total += r["n"]
    lines.append(f"  {'TOTAL':14} {total}")

    # And the converse: churches that DO have PDFs, by policy — a sanity check
    # that 'direct' really is the productive bucket.
    cur.execute(
        f"""
        SELECT COALESCE(p.policy, '(unmeasured)') AS policy, COUNT(*) AS n
        FROM church c
        LEFT JOIN scrape_host_policy p ON p.host = {host_expr}
        WHERE c.state_code = %s
          AND EXISTS (
                SELECT 1 FROM bulletin_source bs
                JOIN bulletin_pdf bp ON bp.bulletin_source_id = bs.bulletin_source_id
                WHERE bs.church_id = c.church_id)
        GROUP BY COALESCE(p.policy, '(unmeasured)')
        ORDER BY n DESC
        """,
        (state,),
    )
    lines.append("")
    lines.append(f"--- {state} churches WITH pdfs, by host policy (control) ---")
    for r in cur.fetchall():
        lines.append(f"  {r['policy']:14} {r['n']}")

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_proxy_value", out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = "diag_proxy_value FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_proxy_value", tb)
        sys.exit(1)
