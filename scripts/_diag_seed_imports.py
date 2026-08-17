"""Isolate where the seed job dies.

The seed one-off failed twice in ~55s and published no readback at all, which
means it never reached main() — the failure is at import time (or the process
is being killed). This walks the imports one at a time and publishes after
each, so the last line that made it to S3 names the culprit.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

steps = []


def record(label, fn):
    try:
        fn()
        steps.append(f"[OK]  {label}")
    except Exception:
        steps.append(f"[ERR] {label}\n{traceback.format_exc()}")
        return False
    return True


def _imp_boto():
    import boto3  # noqa: F401


def _imp_readback():
    from scripts._readback import publish  # noqa: F401


def _imp_pymysql():
    import pymysql  # noqa: F401


def _imp_runscraper():
    import run_bulletin_scraper  # noqa: F401


def _imp_extract():
    import extract_bulletins_to_db99  # noqa: F401


def _seedfile():
    import json

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds",
                     "wi_verified_bulletin_pages.json")
    rows = json.load(open(p, encoding="utf-8"))
    steps.append(f"      seed rows={len(rows)}")


def _connect():
    from extract_bulletins_to_db99 import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM church WHERE state_code='WI'")
    steps.append(f"      WI churches={cur.fetchone()['n']}")
    cur.execute(
        "SELECT COUNT(*) AS n FROM church WHERE state_code='WI' "
        "AND website_url IS NOT NULL AND website_url<>''"
    )
    steps.append(f"      WI with website_url={cur.fetchone()['n']}")
    cur.close()
    conn.close()


record("import boto3", _imp_boto)
record("import scripts._readback", _imp_readback)
record("import pymysql", _imp_pymysql)
record("read seed file", _seedfile)
record("import run_bulletin_scraper", _imp_runscraper)
record("import extract_bulletins_to_db99", _imp_extract)
record("connect db99 + count WI", _connect)

out = "\n".join(steps)
print(out, flush=True)
try:
    from scripts._readback import publish

    publish("diag_seed_imports", out)
except Exception:
    print("readback unavailable:\n" + traceback.format_exc(), flush=True)
