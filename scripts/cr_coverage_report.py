"""
cr_coverage_report.py — Are we scraping churches in every Cherry Road market?

Diffs the Cherry Road city shapes (db99 `cr_market_shape`, synced from Limpar by
refresh_cherry_road_shapes.py) against what's actually in the db99 `church` table
(city + state_code). Reports per-market coverage and freshness, and writes a detail CSV.

Read-only. Run after refresh_cherry_road_shapes.py.

Usage:
    DB_HOST=10.10.0.8 python scripts/cr_coverage_report.py
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.cr_markets import TABLE, norm_city  # noqa: E402
from src.utils.db_connection import get_connection  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = ROOT / "cherry_road_coverage_report.csv"


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # CR city shapes (the geographies we must cover)
            cur.execute(
                f"SELECT project, state_abbr, shape, shape_norm "
                f"FROM {TABLE} WHERE shape_type = 'city'"
            )
            cr_cities = cur.fetchall()

            # What we actually scrape: churches grouped by (state, city)
            cur.execute(
                "SELECT state_code, city, COUNT(*) AS n, MAX(last_scraped_at) AS last "
                "FROM church WHERE city IS NOT NULL AND city <> '' GROUP BY state_code, city"
            )
            covered = {}
            for r in cur.fetchall():
                key = ((r["state_code"] or "").upper(), norm_city(r["city"]))
                n, last = r["n"], r["last"]
                if key in covered:
                    pn, pl = covered[key]
                    last = max(filter(None, [pl, last])) if (pl or last) else None
                    n += pn
                covered[key] = (n, last)
    finally:
        conn.close()

    rows_out, by_project = [], defaultdict(list)
    for c in cr_cities:
        key = ((c["state_abbr"] or "").upper(), c["shape_norm"] or norm_city(c["shape"]))
        n, last = covered.get(key, (0, None))
        rec = {
            "project": c["project"], "state": c["state_abbr"], "city": c["shape"],
            "churches": n, "last_scraped": last or "", "covered": "yes" if n > 0 else "NO",
        }
        rows_out.append(rec)
        by_project[c["project"]].append(n > 0)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["project", "state", "city", "churches", "last_scraped", "covered"])
        w.writeheader()
        w.writerows(rows_out)

    total = len(rows_out)
    cov = sum(1 for r in rows_out if r["covered"] == "yes")
    pct = (100 * cov // total) if total else 0
    print(f"[OK] CR city shapes: {total} | covered (>=1 church): {cov} ({pct}%) | gaps: {total - cov}")

    full = sum(1 for v in by_project.values() if all(v))
    zero_projects = [p for p, v in by_project.items() if not any(v)]
    partial = len(by_project) - full - len(zero_projects)
    print(f"[OK] CR markets by city coverage: fully={full}, partial={partial}, ZERO={len(zero_projects)}")
    if zero_projects:
        print("[--] Markets with ZERO city coverage:")
        for p in sorted(zero_projects):
            print(f"       {p}")

    gaps_by_state = defaultdict(int)
    for r in rows_out:
        if r["covered"] == "NO":
            gaps_by_state[r["state"]] += 1
    if gaps_by_state:
        print("[--] Uncovered CR cities by state:")
        for st, c in sorted(gaps_by_state.items(), key=lambda x: -x[1]):
            print(f"       {st}: {c}")
    print(f"[OK] Detail -> {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
