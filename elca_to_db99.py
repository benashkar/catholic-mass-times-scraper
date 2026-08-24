"""Load ELCA congregations into db99 — the first non-Catholic source.

The corpus has been Catholic-only, and not merely by convention: until this
session `church` had no `denomination` column at all. Wisconsin is the most
Lutheran state in the country, so it is where the first non-Catholic adapter
pays best -- 624 ELCA congregations, 513 of them carrying their own website,
against 980 Catholic parishes already in the table.

Additive by construction. Nothing about the Catholic path changes: no existing
row is rewritten, no column is repurposed, and the sole UPDATE touches rows this
loader itself created.

Two rules that matter more than they look:

**Never merge across denominations.** `decide_match` finds churches within 150m
with a similar name. Downtown blocks and shared campuses put a Lutheran
congregation inside 150m of a Catholic parish routinely, and `bulletin_pdf` rows
hang off `church_id` -- so one bad merge silently attributes one congregation's
bulletins, and everyone named in them, to a different church. Every existing row
is now stamped `denomination='catholic'`, so the guard is simple and reliable:
a candidate of a different denomination is not a match, whatever the distance.

**Dry-run is the default.** `--commit` is opt-in. The repo's failure history is
jobs that reported success while doing nothing (or the wrong thing), so read the
MATCH/NEW/AMBIGUOUS counts before writing.

Usage:
    python -u elca_to_db99.py --state WI                 # dry run
    python -u elca_to_db99.py --state WI --commit
    python -u elca_to_db99.py --all-states --commit
"""

import argparse
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from discovermass_to_db99 import decide_match  # noqa: E402
from src.scrapers.elca import STATE_NAMES, parse_state  # noqa: E402


def get_conn():
    from extract_bulletins_to_db99 import get_connection

    return get_connection()


def slugify(name, city, state, source_id):
    """Stable, unique, and traceable back to the source record.

    `church.slug` is UNIQUE, which makes it the natural idempotency key: a
    re-run of this loader must update, never duplicate. The ELCA LocationID is a
    UUID and already unique, so it alone would do -- the readable prefix is
    there so a human reading the table can tell what a row is without a join.
    """
    base = f"{name}-{city}-{state}"
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:120]
    return f"elca-{base}-{(source_id or '')[:8]}".strip("-")


def existing_in_state(cur, state):
    cur.execute(
        """SELECT church_id, name, city, state_code, latitude, longitude,
                  denomination, website_url
           FROM church WHERE state_code = %s""",
        (state,),
    )
    return cur.fetchall()


def load_state(cur, state, commit, limit=None):
    parsed = parse_state(state)
    if limit:
        parsed = parsed[:limit]
    existing = existing_in_state(cur, state)
    print(f"\n=== {state}: {len(parsed)} ELCA congregations, "
          f"{len(existing):,} churches already in db99 ===", flush=True)

    # Only same-denomination rows are candidates. See the module docstring: a
    # cross-denomination merge is silent and corrupts attribution downstream.
    same_denom = [e for e in existing
                  if (e.get("denomination") or "").lower() == "elca"]

    counts = {"NEW": 0, "MATCH": 0, "AMBIGUOUS": 0, "SKIP_NO_SITE": 0}
    ambiguous = []
    inserted = updated = 0

    for p in parsed:
        ch = p["church"]
        d = decide_match({"church": ch}, same_denom)
        dec = d["decision"]

        if dec == "AMBIGUOUS":
            counts["AMBIGUOUS"] += 1
            ambiguous.append((ch["name"], ch["city"], d["reason"]))
            continue

        if dec == "MATCH":
            counts["MATCH"] += 1
            if commit:
                # Fill blanks only. A previously-loaded row may have been
                # corrected by hand or by the URL-repair job, and overwriting
                # that with the directory's value would undo the correction on
                # every re-run.
                cur.execute(
                    """UPDATE church
                       SET website_url = COALESCE(NULLIF(website_url,''), %s),
                           latitude    = COALESCE(latitude, %s),
                           longitude   = COALESCE(longitude, %s),
                           phone       = COALESCE(NULLIF(phone,''), %s),
                           denomination = COALESCE(denomination, 'elca'),
                           source_provider = COALESCE(source_provider, 'elca')
                       WHERE church_id = %s""",
                    (ch["website_url"], ch["latitude"], ch["longitude"],
                     ch["phone"], d["church_id"]),
                )
                updated += 1
            continue

        counts["NEW"] += 1
        if not commit:
            continue
        slug = slugify(ch["name"], ch["city"] or "", state, ch["source_id"])
        try:
            cur.execute(
                """INSERT INTO church
                     (slug, name, street, city, state_code, postal_code,
                      postal_code_clean, latitude, longitude, phone,
                      website_url, denomination, source_provider, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                   ON DUPLICATE KEY UPDATE
                     website_url = COALESCE(NULLIF(church.website_url,''), VALUES(website_url)),
                     latitude    = COALESCE(church.latitude, VALUES(latitude)),
                     longitude   = COALESCE(church.longitude, VALUES(longitude)),
                     phone       = COALESCE(NULLIF(church.phone,''), VALUES(phone)),
                     denomination = COALESCE(church.denomination, VALUES(denomination)),
                     source_provider = COALESCE(church.source_provider, VALUES(source_provider))""",
                (slug, ch["name"], ch["street"], ch["city"] or "", state,
                 ch["postal_code"], ch["postal_code_clean"], ch["latitude"],
                 ch["longitude"], ch["phone"], ch["website_url"],
                 "elca", "elca"),
            )
            inserted += 1
        except Exception as e:
            print(f"  [ERR] insert {ch['name']}: {e}", flush=True)

    withsite = sum(1 for p in parsed if p["church"]["website_url"])
    print(f"  NEW={counts['NEW']}  MATCH={counts['MATCH']}  "
          f"AMBIGUOUS={counts['AMBIGUOUS']}", flush=True)
    print(f"  carrying a website_url: {withsite}/{len(parsed)}", flush=True)
    if ambiguous:
        print("  --- AMBIGUOUS (left alone for review) ---", flush=True)
        for n, c, why in ambiguous[:8]:
            print(f"    {str(n)[:36]:36} {str(c)[:16]:16} {why}", flush=True)
    if commit:
        print(f"  [OK] inserted {inserted}, updated {updated}", flush=True)
    else:
        print("  [DRY] nothing written — pass --commit to write", flush=True)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None)
    ap.add_argument("--all-states", action="store_true")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if not args.state and not args.all_states:
        ap.error("give --state XX or --all-states")

    states = ([s.upper() for s in STATE_NAMES] if args.all_states
              else [args.state.upper()])

    conn = get_conn()
    conn.autocommit(True)
    cur = conn.cursor()
    total = {"NEW": 0, "MATCH": 0, "AMBIGUOUS": 0}
    for st in states:
        try:
            c = load_state(cur, st, args.commit, args.limit)
            for k in total:
                total[k] += c.get(k, 0)
        except Exception as e:
            # One bad state must not abandon the other 49.
            print(f"[ERR] {st}: {type(e).__name__}: {e}", flush=True)
    print(f"\n==== TOTAL  NEW={total['NEW']}  MATCH={total['MATCH']}  "
          f"AMBIGUOUS={total['AMBIGUOUS']} ====", flush=True)
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
