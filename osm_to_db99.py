"""Load OpenStreetMap places of worship into db99 — the widest net available.

ELCA gives one denomination cleanly. OSM gives every denomination messily, and
in Wisconsin that is worth 1,447 congregations db99 does not hold, 441 of them
carrying a `website` tag. It reaches the congregations with no denominational
directory at all: independent Baptist, non-denominational, Pentecostal,
Assemblies of God, and the long tail of single-congregation churches.

**Attribution.** OSM data is ODbL. Anything published from these rows must
credit OpenStreetMap contributors. That obligation rides with the data, which is
why every row this loader writes is stamped `source_provider='osm'` — so the
subset needing attribution stays identifiable forever rather than dissolving
into the corpus.

**Denomination is dirty and that is fine.** OSM's `denomination` tag is
free-text: `lutheran`, `evangelical_lutheran`, `roman_catholic`, `catholic`,
`united_methodist`, `methodist` all appear, and 30% of named entries have no
denomination at all. It is normalised lightly here — never guessed. An entry
with no denomination is stored as `christian`, not as somebody's best guess,
because a wrong denomination silently disables the cross-denomination merge
guard that stops one congregation's bulletins being attributed to another.

**Deduped on geometry, not name.** OSM names and db99 names disagree constantly
("St Paul Lutheran" vs "Saint Paul's Evangelical Lutheran Church"), but a church
building is in one place. Anything within 150m of a church already in db99 is
treated as the same building and skipped -- the same radius the DiscoverMass
loader uses.

Usage:
    python -u osm_to_db99.py --state WI                    # dry run
    python -u osm_to_db99.py --state WI --commit
    python -u osm_to_db99.py --state WI --commit --require-website
    python -u osm_to_db99.py --all-states --commit --require-website
"""

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.scrapers.elca import STATE_NAMES  # noqa: E402  (state-code list only)

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "church-scrapes/1.0 (+ben.ashkar@locallabs.com)"
GEO_MATCH_METERS = 150.0

QUERY = """
[out:json][timeout:180];
area["ISO3166-2"="US-{st}"][admin_level=4]->.a;
(
  node["amenity"="place_of_worship"]["religion"="christian"](area.a);
  way["amenity"="place_of_worship"]["religion"="christian"](area.a);
);
out tags center;
"""

# Light normalisation only. Collapsing "evangelical_lutheran" into "lutheran"
# would be a guess that matters: ELCA and WELS are both evangelical Lutheran and
# emphatically not the same body, and the merge guard keys on this string.
DENOM_FIX = {
    "roman_catholic": "catholic",
    "catholic": "catholic",
    "united_methodist": "united_methodist",
    "methodist": "methodist",
    "lutheran": "lutheran",
    "evangelical_lutheran": "evangelical_lutheran",
    "baptist": "baptist",
    "presbyterian": "presbyterian",
    "episcopal": "episcopal",
    "anglican": "episcopal",
    "pentecostal": "pentecostal",
    "united_church_of_christ": "ucc",
    "nondenominational": "nondenominational",
    "non-denominational": "nondenominational",
    "orthodox": "orthodox",
    "latter-day_saints": "lds",
    "assemblies_of_god": "assemblies_of_god",
}


def fetch_state(state, retries=3):
    """Overpass is a shared free service; back off rather than hammer it."""
    q = QUERY.format(st=state.upper())
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OVERPASS,
                data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r).get("elements", [])
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"overpass failed for {state}: {last}")


def _latlon(e):
    if e.get("lat") is not None:
        return e["lat"], e["lon"]
    c = e.get("center") or {}
    return c.get("lat"), c.get("lon")


def _website(tags):
    u = (tags.get("website") or tags.get("contact:website") or "").strip()
    if not u:
        return None
    u = u.replace("http//", "http://").replace("https//", "https://")
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    return u.split()[0] if " " in u else u


def _haversine_m(a1, o1, a2, o2):
    dl = math.radians(a2 - a1)
    dn = math.radians(o2 - o1)
    h = (
        math.sin(dl / 2) ** 2
        + math.cos(math.radians(a1)) * math.cos(math.radians(a2)) * math.sin(dn / 2) ** 2
    )
    return 6371000 * 2 * math.asin(math.sqrt(h))


def slugify(name, city, state, osm_id):
    base = f"{name}-{city}-{state}"
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:120]
    return f"osm-{base}-{osm_id}".strip("-")


def load_state(cur, state, commit, require_website):
    els = [e for e in fetch_state(state) if (e.get("tags") or {}).get("name")]

    cur.execute(
        "SELECT latitude, longitude FROM church " "WHERE state_code=%s AND latitude IS NOT NULL",
        (state,),
    )
    db = [(float(r["latitude"]), float(r["longitude"])) for r in cur.fetchall()]

    def already_have(lat, lon):
        # Cheap bounding-box prefilter before the trig: ~275m box.
        for a, b in db:
            if abs(a - lat) < 0.0025 and abs(b - lon) < 0.0025:
                if _haversine_m(lat, lon, a, b) <= GEO_MATCH_METERS:
                    return True
        return False

    dup = skipped_nosite = inserted = 0
    new = []
    for e in els:
        lat, lon = _latlon(e)
        if lat is None:
            continue
        if already_have(lat, lon):
            dup += 1
            continue
        tags = e["tags"]
        site = _website(tags)
        if require_website and not site:
            skipped_nosite += 1
            continue
        new.append((e, tags, lat, lon, site))

    print(
        f"\n=== {state}: {len(els)} OSM christian places, " f"{len(db):,} already in db99 ===",
        flush=True,
    )
    print(f"  within 150m of an existing church : {dup}", flush=True)
    if require_website:
        print(f"  net-new but no website tag       : {skipped_nosite}", flush=True)
    print(f"  NET NEW to load                  : {len(new)}", flush=True)

    if not commit:
        for e, tags, lat, lon, site in new[:6]:
            print(
                f"    [DRY] {tags['name'][:34]:34} "
                f"{(tags.get('denomination') or 'unspecified'):20} "
                f"{(site or '')[:40]}",
                flush=True,
            )
        print("  [DRY] nothing written — pass --commit", flush=True)
        return {"new": len(new), "dup": dup, "inserted": 0}

    for e, tags, lat, lon, site in new:
        raw = (tags.get("denomination") or "").lower().strip()
        denom = DENOM_FIX.get(raw, raw or "christian")
        city = (tags.get("addr:city") or "").strip()
        street = " ".join(
            x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x
        ).strip()
        pc = (tags.get("addr:postcode") or "").strip()
        slug = slugify(tags["name"], city, state, f"{e.get('type','n')}{e.get('id')}")
        try:
            cur.execute(
                """INSERT INTO church
                     (slug, name, street, city, state_code, postal_code,
                      postal_code_clean, latitude, longitude, phone,
                      website_url, denomination, source_provider, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'osm',1)
                   ON DUPLICATE KEY UPDATE
                     website_url = COALESCE(NULLIF(church.website_url,''), VALUES(website_url)),
                     latitude    = COALESCE(church.latitude, VALUES(latitude)),
                     longitude   = COALESCE(church.longitude, VALUES(longitude)),
                     denomination = COALESCE(church.denomination, VALUES(denomination))""",
                (
                    slug,
                    tags["name"][:200],
                    street or None,
                    city,
                    state,
                    pc or None,
                    (pc.split("-")[0] or None) if pc else None,
                    lat,
                    lon,
                    (tags.get("phone") or tags.get("contact:phone")),
                    site,
                    denom[:60],
                ),
            )
            inserted += 1
        except Exception as ex:
            print(f"  [ERR] {tags['name'][:40]}: {ex}", flush=True)

    print(
        f"  [OK] inserted {inserted}  (ODbL: credit OpenStreetMap "
        f"contributors when publishing these)",
        flush=True,
    )
    return {"new": len(new), "dup": dup, "inserted": inserted}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state")
    ap.add_argument("--all-states", action="store_true")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument(
        "--require-website",
        action="store_true",
        help="Only load congregations carrying a website tag — "
        "the ones the bulletin pipeline can actually use",
    )
    args = ap.parse_args()
    if not args.state and not args.all_states:
        ap.error("give --state XX or --all-states")

    from extract_bulletins_to_db99 import get_connection

    conn = get_connection()
    conn.autocommit(True)
    cur = conn.cursor()
    states = [s.upper() for s in STATE_NAMES] if args.all_states else [args.state.upper()]
    tot = {"new": 0, "dup": 0, "inserted": 0}
    for st in states:
        try:
            r = load_state(cur, st, args.commit, args.require_website)
            for k in tot:
                tot[k] += r.get(k, 0)
        except Exception as e:
            print(f"[ERR] {st}: {type(e).__name__}: {e}", flush=True)
        if len(states) > 1:
            time.sleep(8)  # Overpass is free and shared; do not hammer it.
    print(
        f"\n==== TOTAL net-new={tot['new']} dup={tot['dup']} " f"inserted={tot['inserted']} ====",
        flush=True,
    )
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
