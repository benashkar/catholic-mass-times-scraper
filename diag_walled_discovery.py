"""Does the walled-host strategy actually yield PDFs, or just HTTP 200s?

A 200 through browser+proxy proves the page is reachable. It does not prove
discovery can find bulletins on it, and those are different claims — the whole
point of today's work was that a church can look reachable and still produce
zero PDFs.

So run the real find_bulletin_page() against known-walled hosts and count what
comes back. Read-only: loads the host policy, discovers, reports. Writes
NOTHING to db99, deliberately — the national sweep is running and bulletin_pdf
has no unique index, so a concurrent write on the same church would duplicate
rows.

Usage (Render one-off):
    python -u diag_walled_discovery.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402

TARGETS = [
    ("WI 28452 St Maximilian Kolbe", "https://stmaxkolbe.org"),
    ("WI 28511 Nativity/Big River", "https://www.stmarysbigriver.com"),
    ("WI 28532 Holy Cross", "https://holycrosswi.org"),
    ("ME hc-catholics", "https://hc-catholics.org"),
    ("ME sjvcatholics", "https://www.sjvcatholics.org"),
]


def main():
    from extract_bulletins_to_db99 import get_connection
    from src.utils import host_policy
    import run_bulletin_scraper as R

    conn = get_connection()
    cur = conn.cursor()
    host_policy.ensure_schema(cur)
    pol = host_policy.load(cur)
    cur.close()
    conn.close()

    lines = [
        "walled-host discovery — does strategy 5 produce PDFs?",
        f"host policies loaded: {len(pol):,}",
        f"PROXY_URL set: {bool(os.environ.get('PROXY_URL'))}",
        f"playwright: {R.HAS_PLAYWRIGHT}   playwright proxy: {bool(R._playwright_proxy())}",
        "",
    ]
    publish("diag_walled_discovery", "\n".join(lines))

    for label, url in TARGETS:
        verdict = host_policy.policy_for(url)
        try:
            res = R.find_bulletin_page(url)
            n = len(res.get("pdf_urls") or [])
            src = res.get("source")
            lines.append(f"{label:30} policy={str(verdict):9} pdfs={n:3} source={src}")
            for p in (res.get("pdf_urls") or [])[:2]:
                lines.append(f"      {p[:104]}")
        except Exception as e:
            lines.append(f"{label:30} policy={str(verdict):9} EXC {type(e).__name__}: {e}")
        publish("diag_walled_discovery", "\n".join(lines))

    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_walled_discovery", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_walled_discovery FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_walled_discovery", tb)
        sys.exit(1)
