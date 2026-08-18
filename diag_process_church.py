"""Call the REAL process_church() on specific churches and report what it does.

Two theories have now been wrong about why 39 of the 86 named WI churches stay
at zero. The tracer showed 28511 discovering 100 PDFs and downloading one
successfully (846KB) on this very box, yet the DB still reads 0 for it after a
full targeted run with the browser budget raised to 3000.

So stop testing pieces. Call the actual production function — the same one the
sweep calls, with a real cursor — and report its returned stats plus any
exception. It either inserts rows or it says why not.

This WRITES to db99, deliberately: it is the production path, and the point is
to see the write happen (or fail).

Usage (Render one-off):
    python -u diag_process_church.py --ids 28511,28491
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="28511,28491")
    args = ap.parse_args()
    ids = [int(x) for x in args.ids.replace(",", " ").split()]

    from extract_bulletins_to_db99 import get_connection, process_church, prewarm_shared_state
    from src.utils import host_policy
    import run_bulletin_scraper as R

    R.WALLED_BROWSER_ENABLED = True
    R.WALLED_BROWSER_BUDGET = 3000

    conn = get_connection()
    conn.autocommit(True)
    cur = conn.cursor()
    host_policy.ensure_schema(cur)
    host_policy.load(cur)
    try:
        prewarm_shared_state()
    except Exception as e:
        print(f"prewarm: {e}", flush=True)

    lines = [
        "process_church() run directly",
        f"walled={R.WALLED_BROWSER_ENABLED} budget={R.WALLED_BROWSER_BUDGET} "
        f"playwright={R.HAS_PLAYWRIGHT} proxy={bool(R._playwright_proxy())}",
    ]
    publish("diag_process_church", "\n".join(lines))

    fmt = ",".join(["%s"] * len(ids))
    cur.execute(
        f"SELECT church_id, slug, name, city, state_code, website_url "
        f"FROM church WHERE church_id IN ({fmt})", ids
    )
    churches = cur.fetchall()

    for ch in churches:
        cid = ch["church_id"]
        lines.append("")
        lines.append(f"--- {cid} {(ch['name'] or '')[:30]}  website={ch['website_url']!r}")
        try:
            stats = process_church(cur, ch)
            lines.append(f"    stats: {stats}")
        except Exception as e:
            lines.append(f"    EXC {type(e).__name__}: {str(e)[:200]}")
            lines.append("    " + traceback.format_exc()[-500:].replace("\n", "\n    "))

        cur.execute(
            "SELECT COUNT(*) n FROM bulletin_pdf bp JOIN bulletin_source bs "
            "ON bs.bulletin_source_id=bp.bulletin_source_id WHERE bs.church_id=%s", (cid,)
        )
        lines.append(f"    bulletin_pdf rows now: {cur.fetchone()['n']}")
        publish("diag_process_church", "\n".join(lines))

    cur.close(); conn.close()
    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_process_church", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_process_church FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_process_church", tb)
        sys.exit(1)
