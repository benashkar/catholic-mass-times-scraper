"""Did mass TIMES move, or only bulletins?

The walled-host work ran extract_bulletins_to_db99.py, which writes only
bulletin_source / bulletin_pdf / bulletin_name. Mass times live in `service`
and come from a different pipeline (run_discovermass_all -> discovermass_to_db99).
So the expectation is that `service` is UNCHANGED — but expectation is not
measurement, and the churches whose websites we just fixed are exactly the ones
whose schedules may also be stale.

Reports: total active services, how many churches have any, when they were last
refreshed, and — the useful bit — how many churches now have a bulletin PDF but
NO active service row. That last number is the actual opportunity.

Read-only.
"""
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _readback import publish  # noqa: E402


def main():
    from extract_bulletins_to_db99 import get_connection
    conn = get_connection(); cur = conn.cursor()

    def one(sql, p=None):
        cur.execute(sql, p or ()); r = cur.fetchone()
        return list(r.values())[0] if isinstance(r, dict) else r[0]

    L = ["mass times (service table) vs bulletins", ""]
    L.append(f"active service rows            {one('SELECT COUNT(*) FROM service WHERE is_active=1'):,}")
    L.append(f"churches with >=1 service      {one('SELECT COUNT(DISTINCT church_id) FROM service WHERE is_active=1'):,}")
    L.append(f"churches with >=1 bulletin_pdf {one('SELECT COUNT(DISTINCT bs.church_id) FROM bulletin_source bs JOIN bulletin_pdf bp ON bp.bulletin_source_id=bs.bulletin_source_id'):,}")
    L.append("")
    L.append("--- when were services last written? ---")
    cur.execute("SELECT DATE(updated_at) d, COUNT(*) n FROM service WHERE updated_at IS NOT NULL GROUP BY DATE(updated_at) ORDER BY d DESC LIMIT 8")
    for r in cur.fetchall(): L.append(f"  {r['d']}  {r['n']:,}")
    L.append("")
    L.append("--- when was church.last_scraped_at (mass-times pipeline) last set? ---")
    cur.execute("SELECT DATE(last_scraped_at) d, COUNT(*) n FROM church WHERE last_scraped_at IS NOT NULL GROUP BY DATE(last_scraped_at) ORDER BY d DESC LIMIT 6")
    for r in cur.fetchall(): L.append(f"  {r['d']}  {r['n']:,}")

    gap = one("""
        SELECT COUNT(DISTINCT c.church_id) FROM church c
        JOIN bulletin_source bs ON bs.church_id=c.church_id
        JOIN bulletin_pdf bp ON bp.bulletin_source_id=bs.bulletin_source_id
        WHERE NOT EXISTS (SELECT 1 FROM service s WHERE s.church_id=c.church_id AND s.is_active=1)
    """)
    L.append("")
    L.append(f"** churches WITH a bulletin PDF but NO active service row: {gap:,} **")
    L.append("   (that is the mass-times opportunity the bulletin work exposed)")

    cur.close(); conn.close()
    out = "\n".join(L); print(out, flush=True); publish("diag_service_freshness", out)


if __name__ == "__main__":
    try: main()
    except SystemExit: raise
    except BaseException:
        tb = "diag_service_freshness FAILED\n" + traceback.format_exc()
        print(tb, flush=True); publish("diag_service_freshness", tb); sys.exit(1)
