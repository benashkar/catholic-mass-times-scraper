"""Measure, from Render, the hosts that recovered locally but not in production.

32 of 86 recovery targets gained PDFs; 54 did not. Every still-zero page tested
here yields bulletins from a workstation — 100 PDFs for Big River, 12 each for
the others, all including the 2026-08-16 edition — yet production recorded 0.
St. Maximilian Kolbe rules out the known-page fallback as the cause: its stored
page is on the SAME host as its website_url, so plain discovery should have
found it.

That leaves egress. This project has been here before: ~4,400 hosts 403 from
Render while returning 200 residentially, and a laptop survey once certified 7
parish sites as healthy while they were failing in production. Hence the rule —
measure from the machine that runs the scraper.

Fetches each URL direct and (if PROXY_URL is set) through the proxy, and reports
both, so the verdict rests on a controlled comparison rather than a guess.

Usage (Render one-off):
    python -u diag_render_egress.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from _readback import publish  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TARGETS = [
    # still-zero parish pages that work from a workstation
    ("28511 big river page", "https://www.stmarysbigriver.com/bulletins"),
    ("28491 dsoll page", "https://www.dsoll.org/bulletins"),
    ("28532 holycrosswi page", "https://holycrosswi.org/bulletins"),
    ("28552 stlouiscaledonia", "https://www.stlouiscaledoniawi.org/bulletin"),
    ("28452 stmaxkolbe", "https://stmaxkolbe.org/bulletin"),
    # the LPi resolver chain the fix depends on
    ("LPi api slug lookup",
     "https://api.parishesonline.com/organizations/slug/st-therese-of-lisieux-church"),
    ("LPi api publications",
     "https://api.parishesonline.com/organizations/0018000000Qc2VWAAZ/publications?type=bulletin"),
    # a CDN that DID work in production (32 churches recovered through it)
    ("LPi container pdf",
     "https://container.parishesonline.com/bulletins/01/0355/20260816B.pdf"),
    # eCatholic file host
    ("eCatholic file",
     "https://files.ecatholic.com/22480/bulletins/20260816.pdf"),
    ("control: example.com", "http://example.com"),
]


def probe(url, proxies):
    try:
        r = requests.get(
            url, headers={"User-Agent": UA}, timeout=40,
            proxies=proxies, allow_redirects=True, stream=True,
        )
        body = r.raw.read(600, decode_content=True) or b""
        kind = "PDF" if body[:5].startswith(b"%PDF") else f"{len(body)}b"
        return f"{r.status_code} {kind}"
    except Exception as e:
        return f"EXC {type(e).__name__}"


def main():
    proxy = os.environ.get("PROXY_URL", "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    lines = ["render egress probe", f"PROXY_URL set: {bool(proxy)}", ""]
    lines.append(f"{'target':26} {'direct':>16}   {'via proxy':>16}")
    lines.append("-" * 64)
    for label, url in TARGETS:
        d = probe(url, None)
        p = probe(url, proxies) if proxies else "-"
        lines.append(f"{label:26} {d:>16}   {p:>16}")

    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_render_egress", out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = "diag_render_egress FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_render_egress", tb)
        sys.exit(1)
