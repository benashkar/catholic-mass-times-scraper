"""Export names-dataset into db99 so the third engine costs no memory.

Why this exists
---------------
NameDataset holds 727,556 first names and 983,826 last names and costs
**~1.86 GB resident** (measured on the 2GB cron plan: 92 MB -> 1,948 MB). The
bulletin sweep already carries spaCy en_core_web_lg plus pdfplumber across two
workers — that is why --workers was cut from 4 to 2 after 27 OOM kills in 56
jobs — so the three-engine consensus cannot run inline.

But the engine only ever asks membership questions: is this word a known first
name, is that word a known surname. Holding 1.86 GB of Python objects to answer
that is the wrong shape, and this project already solved it twice:
ref_ssa_names and ref_census_surnames are db99 tables, not in-process dicts.

So export once, then look up over an index. Near-zero memory, inline in the
sweep, on the existing plan, keeping en_core_web_lg.

Fidelity note: the engine treats a name as "found" only when its country map
holds a positive value, so that same test gates what is exported here — a row
in this table means exactly what first_found/last_found mean in
NameDatasetEngine.

Memory: drains the source dicts with popitem() while inserting, so the process
does not hold both the full dataset and a growing buffer. Peak stays near the
1.9 GB the dataset itself costs.

Usage (Render one-off — give it the box to itself):
    python -u export_name_dataset_to_db99.py --dry-run
    python -u export_name_dataset_to_db99.py
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402

BATCH = 2000


def any_positive_country(entry):
    """Mirror NameDatasetEngine's found-test exactly."""
    return any(v > 0 for v in (entry.get("country", {}) or {}).values())


def top_country(entry):
    c = entry.get("country", {}) or {}
    return max(c, key=c.get) if c else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    cur = conn.cursor()

    # One DDL statement, hoisted above every loop — never per row.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ref_name_dataset (
            name_upper   VARCHAR(100) NOT NULL,
            is_first     TINYINT NOT NULL DEFAULT 0,
            is_last      TINYINT NOT NULL DEFAULT 0,
            first_rank   INT NULL,
            last_rank    INT NULL,
            first_country VARCHAR(8) NULL,
            last_country  VARCHAR(8) NULL,
            PRIMARY KEY (name_upper),
            KEY idx_is_first (is_first),
            KEY idx_is_last (is_last)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    from names_dataset import NameDataset

    nd = NameDataset()
    n_first = len(nd.first_names or {})
    n_last = len(nd.last_names or {})
    lines = [f"names-dataset loaded: {n_first:,} first, {n_last:,} last"]
    print(lines[-1], flush=True)

    if args.dry_run:
        lines.append("DRY RUN — table ensured, nothing written.")
        out = "\n".join(lines)
        print(out, flush=True)
        publish("export_name_dataset", out)
        return

    sql = (
        "INSERT INTO ref_name_dataset "
        "(name_upper, is_first, is_last, first_rank, last_rank, first_country, last_country) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "  is_first = GREATEST(is_first, VALUES(is_first)), "
        "  is_last  = GREATEST(is_last,  VALUES(is_last)), "
        "  first_rank = COALESCE(VALUES(first_rank), first_rank), "
        "  last_rank  = COALESCE(VALUES(last_rank),  last_rank), "
        "  first_country = COALESCE(VALUES(first_country), first_country), "
        "  last_country  = COALESCE(VALUES(last_country),  last_country)"
    )

    def drain(source, is_first):
        """Insert and free as we go — popitem keeps peak memory flat."""
        written = skipped = 0
        batch = []
        while source:
            name, entry = source.popitem()
            if not name or not any_positive_country(entry):
                skipped += 1
                continue
            key = name.strip().upper()[:100]
            if not key:
                skipped += 1
                continue
            rank_map = entry.get("rank", {}) or {}
            rank = min((v for v in rank_map.values() if v), default=None)
            country = top_country(entry)
            if is_first:
                batch.append((key, 1, 0, rank, None, country, None))
            else:
                batch.append((key, 0, 1, None, rank, None, country))
            if len(batch) >= BATCH:
                cur.executemany(sql, batch)
                written += len(batch)
                batch = []
        if batch:
            cur.executemany(sql, batch)
            written += len(batch)
        return written, skipped

    w1, s1 = drain(nd.first_names, True)
    lines.append(f"first names: written={w1:,} skipped={s1:,}")
    print(lines[-1], flush=True)
    publish("export_name_dataset", "\n".join(lines))

    w2, s2 = drain(nd.last_names, False)
    lines.append(f"last  names: written={w2:,} skipped={s2:,}")
    print(lines[-1], flush=True)

    cur.execute("SELECT COUNT(*) AS n FROM ref_name_dataset")
    total = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM ref_name_dataset WHERE is_first = 1")
    tf = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM ref_name_dataset WHERE is_last = 1")
    tl = cur.fetchone()["n"]
    lines.append(f"ref_name_dataset rows={total:,}  is_first={tf:,}  is_last={tl:,}")

    cur.close()
    conn.close()

    out = "\n".join(lines)
    print(out, flush=True)
    publish("export_name_dataset", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "export_name_dataset FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("export_name_dataset", tb)
        sys.exit(1)
