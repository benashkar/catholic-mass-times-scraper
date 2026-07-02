"""
refresh_cherry_road_shapes.py — Sync Cherry Road geography from Limpar into db99.

Limpar is the SOURCE OF TRUTH for which geographies belong to each Cherry Road project.
This script reads that mapping (read-only) and replaces the db99 `cr_market_shape` table
atomically, so the church pipeline always has the current relationships to join coverage
against. Re-run any time the Limpar project<->shape relationships change.

Also writes `cherry_road_project_shapes.csv` at the project root as a convenience export.

Prereqs: dev VPN UP + AWS creds. Limpar read is READ-ONLY; db99 write is a full replace
of one standalone table (cr_market_shape) — no impact on church/bulletin tables.

Usage:
    DB_HOST=10.10.0.8 python scripts/refresh_cherry_road_shapes.py
"""

import csv
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.cr_markets import (  # noqa: E402
    LIMPAR_COLUMNS,
    LIMPAR_QUERY,
    NETWORK_FILTER,
    TABLE,
    TABLE_DDL,
    get_limpar_connection,
    norm_city,
)
from src.utils.db_connection import get_connection  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = ROOT / "cherry_road_project_shapes.csv"


def fetch_from_limpar():
    conn = get_limpar_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(LIMPAR_QUERY, (NETWORK_FILTER,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def replace_db99_table(rows):
    """Atomically replace cr_market_shape with the freshly-pulled rows."""
    refreshed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(TABLE_DDL)
            # Full replace inside one transaction — table holds only CR data.
            cur.execute(f"DELETE FROM {TABLE}")
            if rows:
                cur.executemany(
                    f"INSERT INTO {TABLE} "
                    "(network, project, project_pipeline_id, project_shape_type, "
                    " shape, shape_type, state_abbr, shape_norm, refreshed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            r["network"],
                            r["project"],
                            r["project_pipeline_id"],
                            r["project_shape_type"],
                            r["shape"],
                            r["shape_type"],
                            r["state_abbr"],
                            norm_city(r["shape"]) if r["shape_type"] == "city" else None,
                            refreshed_at,
                        )
                        for r in rows
                    ],
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return refreshed_at


def write_csv(rows):
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(LIMPAR_COLUMNS)
        w.writerows([[r[c] for c in LIMPAR_COLUMNS] for r in rows])


def main():
    rows = fetch_from_limpar()
    refreshed_at = replace_db99_table(rows)
    write_csv(rows)

    projects = {r["project"] for r in rows}
    by_type = Counter(r["shape_type"] for r in rows)
    print(
        f"[OK] Limpar -> db99 {TABLE}: replaced with {len(rows)} shapes (refreshed_at={refreshed_at} UTC)"
    )
    print(f"[OK] CSV export -> {OUT_CSV}")
    print(f"[OK] Projects: {len(projects)} | Total shapes: {len(rows)}")
    print("[OK] Shapes by type:")
    for t, c in by_type.most_common():
        print(f"       {t or '(null)'}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
