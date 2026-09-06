"""Trace the EXACT production discovery path for specific churches.

The 86 targeted churches were run with --walled-browser and only 2 more
recovered, yet direct probing shows 28511 has 100 bulletins available at
stmarysbigriver.com/bulletins and 28491 has 12 at dsoll.org/bulletins. So the
URLs are right and the strategy works in isolation — something in the
production path is dropping them.

Reproduce that path exactly, step by step, for named church_ids:
  1. the stored website_url and the host policy verdict for it
  2. find_bulletin_page(website_url) — what the primary attempt returns
  3. the known bulletin page and whether _different_host() lets the fallback fire
  4. find_bulletin_page(known_page) — what the fallback returns
  5. whether the first discovered PDF actually DOWNLOADS

Read-only: discovers and fetches, writes nothing to db99.

Usage (Render one-off):
    python -u diag_church_path.py --ids 28511,28491,28533,28440
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="28511,28491,28533,28440")
    args = ap.parse_args()
    ids = [int(x) for x in args.ids.replace(",", " ").split()]

    import run_bulletin_scraper as R
    from extract_bulletins_to_db99 import (
        _different_host,
        download_pdf_to_memory,
        get_connection,
        get_known_bulletin_page,
    )
    from src.utils import host_policy

    R.WALLED_BROWSER_ENABLED = True

    conn = get_connection()
    cur = conn.cursor()
    host_policy.ensure_schema(cur)
    pol = host_policy.load(cur)

    lines = [
        "production discovery path, traced per church",
        f"host policies: {len(pol):,}   walled_enabled: {R.WALLED_BROWSER_ENABLED}   "
        f"playwright: {R.HAS_PLAYWRIGHT}   proxy: {bool(R._playwright_proxy())}",
    ]
    publish("diag_church_path", "\n".join(lines))

    fmt = ",".join(["%s"] * len(ids))
    cur.execute(f"SELECT church_id, name, website_url FROM church WHERE church_id IN ({fmt})", ids)
    rows = cur.fetchall()

    for r in rows:
        cid, name, web = r["church_id"], (r["name"] or "")[:28], r["website_url"]
        lines.append("")
        lines.append(f"--- {cid} {name}")
        lines.append(f"    website_url : {web!r}")
        lines.append(f"    policy      : {host_policy.policy_for(web or '')}")

        try:
            res = R.find_bulletin_page(web)
            n = len(res.get("pdf_urls") or [])
            lines.append(f"    primary     : {n} pdfs, source={res.get('source')}")
        except Exception as e:
            res, n = {}, 0
            lines.append(f"    primary     : EXC {type(e).__name__}: {str(e)[:60]}")

        known = get_known_bulletin_page(cur, cid)
        lines.append(f"    known page  : {known!r}")
        if not n:
            diff = _different_host(known, web) if known else False
            lines.append(f"    diff host?  : {diff}")
            if known and diff:
                try:
                    alt = R.find_bulletin_page(known)
                    n2 = len(alt.get("pdf_urls") or [])
                    lines.append(f"    fallback    : {n2} pdfs, source={alt.get('source')}")
                    if n2:
                        res, n = alt, n2
                except Exception as e:
                    lines.append(f"    fallback    : EXC {type(e).__name__}: {str(e)[:60]}")

        if n:
            first = (res.get("pdf_urls") or [])[0]
            lines.append(f"    first pdf   : {first[:96]}")
            blob = download_pdf_to_memory(first)
            lines.append(
                f"    download    : {'OK ' + str(len(blob)) + ' bytes' if blob else 'FAILED (None)'}"
            )
        publish("diag_church_path", "\n".join(lines))

    cur.close()
    conn.close()
    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_church_path", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_church_path FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_church_path", tb)
        sys.exit(1)
