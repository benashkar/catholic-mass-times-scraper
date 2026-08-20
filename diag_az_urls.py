"""What exactly is in AZ's broken website_url rows, and is anything recoverable?

AZ is flat because ~half its zero-PDF churches have an unusable website_url.
Before proposing any repair, look at the actual values: do they carry a
signature (the CatholicIndex shape run_resolve_urls.py handles), or are they the
masstimes shape that now 404s? And do these churches have any OTHER usable
signal — a source_url, or an existing bulletin_source page — we could pivot on?

Read-only. Usage:
    python -u diag_az_urls.py --state AZ
"""
import argparse, os, sys, traceback
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _readback import publish  # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--state", default="AZ")
    a = ap.parse_args(); st = a.state.upper()
    from extract_bulletins_to_db99 import get_connection
    conn = get_connection(); cur = conn.cursor()
    cur.execute(
        """
        SELECT c.church_id, c.name, c.website_url, c.source_url,
               (SELECT bs.bulletin_page_url FROM bulletin_source bs
                WHERE bs.church_id=c.church_id LIMIT 1) AS page
        FROM church c
        WHERE c.state_code=%s
          AND NOT EXISTS (SELECT 1 FROM bulletin_source bs
                          JOIN bulletin_pdf bp ON bp.bulletin_source_id=bs.bulletin_source_id
                          WHERE bs.church_id=c.church_id)
          AND (c.website_url IS NULL OR c.website_url='' OR c.website_url='#'
               OR c.website_url NOT LIKE 'http%%')
        """, (st,))
    rows = cur.fetchall()
    shapes = Counter(); has_src = 0; has_page = 0; sig = 0
    lines = [f"{st}: zero-PDF churches with an unusable website_url = {len(rows)}", ""]
    for r in rows:
        u = (r["website_url"] or "")
        shapes["empty" if not u.strip() else ("placeholder #" if u.strip()=="#" else
              ("/api/out" if "/api/out" in u else "other"))] += 1
        if "sig=" in u: sig += 1
        if (r["source_url"] or "").startswith("http"): has_src += 1
        if (r["page"] or "").startswith("http"): has_page += 1
    lines.append("--- shapes ---")
    for k, n in shapes.most_common(): lines.append(f"  {k:16} {n}")
    lines.append("")
    lines.append(f"carry a &sig= (the CatholicIndex shape run_resolve_urls handles): {sig}")
    lines.append(f"have a usable source_url to pivot on:                            {has_src}")
    lines.append(f"already have a stored bulletin_page_url:                         {has_page}")
    lines.append("")
    lines.append("--- samples ---")
    for r in rows[:6]:
        lines.append(f"  {r['church_id']} {(r['name'] or '')[:22]}")
        lines.append(f"     website: {(r['website_url'] or '')[:78]!r}")
        lines.append(f"     source : {(r['source_url'] or '')[:78]!r}")
        lines.append(f"     page   : {(r['page'] or '')[:78]!r}")
    cur.close(); conn.close()
    out = "\n".join(lines); print(out, flush=True); publish("diag_az_urls", out)


if __name__ == "__main__":
    try: main()
    except SystemExit: raise
    except BaseException:
        tb = "diag_az_urls FAILED\n" + traceback.format_exc()
        print(tb, flush=True); publish("diag_az_urls", tb); sys.exit(1)
