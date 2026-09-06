"""Find churches whose stored website_url is not a usable URL.

Arizona is flat at 39.9% while every other focus state moves. Its host
breakdown shows 101 of 197 zero-PDF churches sharing one "host" of
`out?id=us-az-ak-chin-...` — which is a query-string fragment, not a hostname.
Those rows have a corrupted website_url, so no scraping strategy can ever help
them: the input is broken, not the target.

Reports, per state, how many zero-PDF churches have a website_url that is not
fetchable, grouped by failure shape, with examples. Read-only.

Usage (Render one-off):
    python -u diag_bad_urls.py --state AZ
    python -u diag_bad_urls.py            # all states, summary only
"""

import argparse
import os
import re
import sys
import traceback
from collections import Counter
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402


def classify(u):
    """Why is this website_url unusable? None means it looks fine."""
    if not u or not u.strip():
        return "empty"
    u = u.strip()
    if u in ("#", "-", "n/a", "N/A"):
        return "placeholder"
    if not u.lower().startswith(("http://", "https://")):
        return "no scheme"
    try:
        p = urlparse(u)
    except Exception:
        return "unparseable"
    h = p.netloc
    if not h:
        return "no host"
    if " " in h or "\t" in h or "@" in h:
        return "malformed host"
    if "." not in h:
        return "host has no dot"
    if not re.match(r"^[A-Za-z0-9.\-:]+$", h):
        return "illegal chars in host"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="")
    args = ap.parse_args()

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()

    where = "WHERE 1=1"
    params = []
    if args.state:
        where += " AND c.state_code = %s"
        params.append(args.state.upper())

    cur.execute(
        f"""
        SELECT c.state_code, c.church_id, c.name, c.website_url
        FROM church c
        {where}
          AND NOT EXISTS (
                SELECT 1 FROM bulletin_source bs
                JOIN bulletin_pdf bp ON bp.bulletin_source_id = bs.bulletin_source_id
                WHERE bs.church_id = c.church_id)
        """,
        params,
    )
    rows = cur.fetchall()

    kinds = Counter()
    by_state = Counter()
    examples = {}
    for r in rows:
        k = classify(r["website_url"])
        if k:
            kinds[k] += 1
            by_state[r["state_code"]] += 1
            examples.setdefault(k, []).append(
                f"{r['church_id']} {(r['name'] or '')[:24]} -> {(r['website_url'] or '')[:70]!r}"
            )

    lines = [
        "unusable website_url among zero-PDF churches"
        + (f" — state={args.state.upper()}" if args.state else " — ALL STATES"),
        f"zero-PDF churches examined: {len(rows):,}",
        f"with an UNUSABLE website_url: {sum(kinds.values()):,}",
        "",
        "--- by failure shape ---",
    ]
    for k, n in kinds.most_common():
        lines.append(f"  {k:22} {n:6}")
        for ex in examples[k][:3]:
            lines.append(f"      {ex}")

    if not args.state:
        lines.append("")
        lines.append("--- worst states ---")
        for st, n in by_state.most_common(15):
            lines.append(f"  {st or '??':4} {n:6}")

    cur.close()
    conn.close()
    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_bad_urls", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_bad_urls FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_bad_urls", tb)
        sys.exit(1)
