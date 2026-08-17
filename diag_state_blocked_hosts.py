"""Which hosts are holding a state's churches hostage, and how were they judged?

Maine has 203 churches with a bulletin source and 13 with a PDF — 6.4%, about a
third of the next-worst state. 185 of its 205 zero-PDF churches sit on hosts
labelled `blocked`. Ninety percent concentration is not 185 independent
failures; small dioceses share a platform, so a handful of hosts probably
account for nearly all of it.

That matters because `blocked` is the verdict that says never spend GB here
again, and some of those verdicts were recorded while our own proxy was dead.
This lists the hosts by how many churches they gate, with the recorded
direct/proxy statuses and WHEN the verdict was taken, so a re-measurement can
be aimed at a few hosts rather than a whole estate.

Read-only.

Usage (Render one-off):
    python -u diag_state_blocked_hosts.py --state ME
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="ME")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    state = args.state.upper()

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()

    host_expr = "SUBSTRING_INDEX(SUBSTRING_INDEX(c.website_url, '/', 3), '/', -1)"
    cur.execute(
        f"""
        SELECT {host_expr} AS host,
               COUNT(*) AS churches,
               MAX(p.policy)        AS policy,
               MAX(p.direct_status) AS direct_status,
               MAX(p.proxy_status)  AS proxy_status,
               MAX(p.checked_at)    AS checked_at
        FROM church c
        LEFT JOIN scrape_host_policy p ON p.host = {host_expr}
        WHERE c.state_code = %s
          AND c.website_url IS NOT NULL AND c.website_url <> ''
          AND NOT EXISTS (
                SELECT 1 FROM bulletin_source bs
                JOIN bulletin_pdf bp ON bp.bulletin_source_id = bs.bulletin_source_id
                WHERE bs.church_id = c.church_id)
        GROUP BY host
        ORDER BY churches DESC
        """,
        (state,),
    )
    rows = cur.fetchall()

    total = sum(r["churches"] for r in rows)
    lines = [
        f"{state}: hosts gating churches with zero PDFs",
        f"distinct hosts {len(rows)}   churches {total}",
        "",
        f"{'churches':>8}  {'policy':12} {'direct':>8} {'proxy':>10}  {'checked':10}  host",
        "-" * 92,
    ]
    covered = 0
    for r in rows[: args.top]:
        covered += r["churches"]
        chk = str(r["checked_at"])[:10] if r["checked_at"] else "-"
        lines.append(
            f"{r['churches']:8}  {(r['policy'] or '(none)'):12} "
            f"{str(r['direct_status'] or '-'):>8} {str(r['proxy_status'] or '-'):>10}  "
            f"{chk:10}  {(r['host'] or '')[:44]}"
        )
    lines.append("")
    lines.append(
        f"top {min(args.top, len(rows))} hosts cover {covered} of {total} churches "
        f"({100.0 * covered / max(1, total):.0f}%)"
    )

    # The verdicts worth re-testing: blocked, but the proxy leg never returned a
    # real HTTP code, so the host was blamed for our account failing.
    cur.execute(
        f"""
        SELECT COUNT(DISTINCT {host_expr}) AS hosts, COUNT(*) AS churches
        FROM church c
        JOIN scrape_host_policy p ON p.host = {host_expr}
        WHERE c.state_code = %s AND p.policy = 'blocked'
          AND (p.proxy_status IS NULL OR p.proxy_status NOT REGEXP '^[0-9]{{3}}$')
        """,
        (state,),
    )
    r = cur.fetchone()
    lines.append("")
    lines.append(
        f"suspect verdicts (blocked, proxy leg not a real HTTP code): "
        f"{r['hosts']} hosts / {r['churches']} churches"
    )

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_state_blocked_hosts", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_state_blocked_hosts FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_state_blocked_hosts", tb)
        sys.exit(1)
