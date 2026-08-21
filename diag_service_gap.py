"""Characterise the 144 churches that have a bulletin PDF but no active service row.

Before running any fix, find out WHY they have no schedule — the answer decides
the tool. Possibilities: never scraped by the mass-times pipeline; scraped but
the source returned zero services; services exist but all is_active=0 (retired
by a stale-cleanup); or no DiscoverMass slug to scrape from at all.

Read-only.
"""
import os, sys, traceback
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _readback import publish  # noqa: E402


def main():
    from extract_bulletins_to_db99 import get_connection
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT c.church_id, c.state_code, c.name, c.slug, c.website_url,
               c.source_url, c.last_scraped_at,
               (SELECT COUNT(*) FROM service s WHERE s.church_id=c.church_id) AS all_svc,
               (SELECT COUNT(*) FROM service s WHERE s.church_id=c.church_id AND s.is_active=1) AS act_svc
        FROM church c
        JOIN bulletin_source bs ON bs.church_id=c.church_id
        JOIN bulletin_pdf bp ON bp.bulletin_source_id=bs.bulletin_source_id
        WHERE NOT EXISTS (SELECT 1 FROM service s WHERE s.church_id=c.church_id AND s.is_active=1)
        GROUP BY c.church_id
    """)
    rows = cur.fetchall()
    shapes, states = Counter(), Counter()
    for r in rows:
        states[r["state_code"]] += 1
        if r["all_svc"] and not r["act_svc"]:
            shapes["has services but ALL is_active=0 (retired)"] += 1
        elif not r["last_scraped_at"]:
            shapes["never scraped by mass-times pipeline"] += 1
        else:
            shapes["scraped, but source returned no services"] += 1

    L = [f"churches with a bulletin PDF but NO active service row: {len(rows)}", ""]
    L.append("--- why ---")
    for k, n in shapes.most_common(): L.append(f"  {k:46} {n}")
    L.append("")
    L.append("--- by state ---")
    L.append("  " + "  ".join(f"{k}:{v}" for k, v in states.most_common(18)))
    L.append("")
    L.append("--- samples ---")
    for r in rows[:10]:
        L.append(f"  {r['church_id']:6} {r['state_code']} {(r['name'] or '')[:26]:28} "
                 f"svc={r['all_svc']}/{r['act_svc']} scraped={str(r['last_scraped_at'])[:10]}")
        L.append(f"         slug={(r['slug'] or '')[:60]}")
    ids = ",".join(str(r["church_id"]) for r in rows)
    L.append("")
    L.append("church_ids:")
    L.append(ids)
    cur.close(); conn.close()
    out = "\n".join(L); print(out, flush=True); publish("diag_service_gap", out)


if __name__ == "__main__":
    try: main()
    except SystemExit: raise
    except BaseException:
        tb = "diag_service_gap FAILED\n" + traceback.format_exc()
        print(tb, flush=True); publish("diag_service_gap", tb); sys.exit(1)
