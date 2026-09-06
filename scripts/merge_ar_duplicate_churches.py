"""
merge_ar_duplicate_churches.py — One-off: merge 2 pre-existing duplicate AR church rows.

Surfaced by the DiscoverMass dry-run as "ambiguous" (two church rows at the same
coordinates). These are CatholicIndex-era duplicates the dashboard shows twice.

  De Queen (St. Barbara):  keep #1071 (newer scrape), delete #1070 (0 bulletin names).
  Jonesboro (Blessed Sac): keep #1116. PRESERVE ALL of #1115's names by moving its
                           bulletin_pdf rows (which carry the names) from source 174 ->
                           #1116's source 175, then delete the now-empty source 174 +
                           #1115's services + church row. (bulletin_source.church_id is
                           UNIQUE so the source row can't be repointed, but bulletin_pdf
                           has only a PK, so the PDFs+names move freely. Zero names lost.)

Transaction-guarded: auto-ROLLBACK unless the bulletin_name total is UNCHANGED (no names
lost), #1116 ends with 274 names (206 + #1115's 68), and survivors are {1071, 1116}.

Usage: DB_HOST=10.10.0.8 python scripts/merge_ar_duplicate_churches.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils.db_connection import get_connection  # noqa: E402


def main():
    conn = get_connection(autocommit=False)
    cur = conn.cursor()

    def total_names():
        cur.execute("SELECT COUNT(*) n FROM bulletin_name")
        return cur.fetchone()["n"]

    def names_for(cid):
        cur.execute(
            "SELECT COUNT(*) n FROM bulletin_source bs "
            "JOIN bulletin_pdf bp ON bp.bulletin_source_id=bs.bulletin_source_id "
            "JOIN bulletin_name bn ON bn.bulletin_pdf_id=bp.bulletin_pdf_id "
            "WHERE bs.church_id=%s",
            (cid,),
        )
        return cur.fetchone()["n"]

    before_total = total_names()
    print(f"[pre ] total bulletin_name rows: {before_total}")
    print(
        f"[pre ] names: #1115={names_for(1115)} #1116={names_for(1116)} "
        f"#1070={names_for(1070)} #1071={names_for(1071)}"
    )

    try:
        # De Queen: delete older dupe #1070 (0 names)
        cur.execute("DELETE FROM bulletin_source WHERE bulletin_source_id=28639")
        cur.execute("DELETE FROM service WHERE church_id=1070")
        cur.execute("DELETE FROM church WHERE church_id=1070")
        # Jonesboro: PRESERVE #1115's names by moving its PDFs onto #1116's source 175,
        # then delete the emptied source 174 + #1115's services + church row.
        cur.execute("UPDATE bulletin_pdf SET bulletin_source_id=175 WHERE bulletin_source_id=174")
        cur.execute("DELETE FROM bulletin_source WHERE bulletin_source_id=174")
        cur.execute("DELETE FROM service WHERE church_id=1115")
        cur.execute("DELETE FROM church WHERE church_id=1115")

        after_total = total_names()
        rem_js = names_for(1116)
        cur.execute("SELECT church_id FROM church WHERE church_id IN (1070,1071,1115,1116)")
        survivors = sorted(r["church_id"] for r in cur.fetchall())
        print(
            f"[post] total bulletin_name rows: {after_total} (delta={after_total-before_total}; must be 0)"
        )
        print(f"[post] Jonesboro #1116 names now: {rem_js} (expected 274 = 206+68)")
        print(f"[post] survivors among the 4: {survivors}")

        if after_total == before_total and rem_js == 274 and survivors == [1071, 1116]:
            conn.commit()
            print(
                "[OK] COMMITTED — duplicates merged, ALL names preserved (moved to #1116), 0 lost"
            )
            return 0
        conn.rollback()
        print("[ERR] anomaly -> ROLLED BACK")
        return 1
    except Exception as e:
        conn.rollback()
        print("[ERR] ROLLED BACK:", str(e)[:160])
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
