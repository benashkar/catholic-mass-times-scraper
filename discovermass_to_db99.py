"""
discovermass_to_db99.py — MATCH-OR-INSERT DiscoverMass parishes into db99.

PURPOSE
    CatholicIndex.org is dead (hard Cloudflare challenge since ~2026-04). The
    `church` + `service` tables on db99 are frozen with ~23,053 CatholicIndex
    rows, and those church_id values are referenced by other tables
    (bulletin_source, etc.). We are switching the mass-times source to
    DiscoverMass, but DiscoverMass slugs DO NOT match CatholicIndex slugs, so a
    plain slug-based UPSERT (the path scrape_to_db99.py uses) would INSERT a
    second, duplicate row for every parish instead of refreshing the existing
    one — orphaning bulletin links and double-listing parishes on the dashboard.

    This script therefore does MATCH-OR-INSERT:
        enumerate_parishes(state) -> parse_parish(each) -> for each parish:
          1. Try to MATCH an existing church row (geo proximity, then
             normalized name+city+state). If matched -> UPDATE that row in
             place, preserving its church_id (and thus its bulletin links).
          2. If no confident match -> INSERT a new church (DiscoverMass slug).
          3. If AMBIGUOUS (multiple geo candidates, or a geo hit whose name is
             very different) -> FLAG, do not auto-merge, count separately.

    It MIRRORS scrape_to_db99.py's column mapping for `church` and its `service`
    UPSERT handling, with one critical adaptation (see SERVICE_ID note below).

DRY-RUN BY DEFAULT
    Running with no flags performs every step EXCEPT db writes: it fetches
    existing churches read-only, builds the full plan in memory, and prints a
    report. The actual write path is implemented in `_commit_plan()` but is only
    invoked when `--commit` is passed. Phase 2 = dry-run only; do NOT pass
    --commit until a human signs off on the match thresholds.

SERVICE_ID NOTE (critical correctness issue, differs from scrape_to_db99.py)
    The `service` table's UPSERT dedup key is the UNIQUE index
    (church_id, source_service_id). CatholicIndex supplies a distinct
    serviceId per service, so its rows never collide. DiscoverMass has NO
    per-service id (parse_parish emits serviceId=""). If we wrote "" for every
    service the way scrape_to_db99.py does, ALL of a parish's services would
    collide on (church_id, "") and only ONE would survive. So here we synthesize
    a STABLE deterministic source_service_id from the service content
    (category|day|timeStart|index) so each service gets its own row and re-runs
    are idempotent. See `_synth_service_id`.

USAGE
    python discovermass_to_db99.py --state AR                 # dry-run report (default)
    python discovermass_to_db99.py --state AR --limit 10      # dry-run, first 10
    python discovermass_to_db99.py --state AR --commit        # DO NOT USE in Phase 2
"""

import argparse
import hashlib
import math
import os
import sys
import time
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reuse the exact same connection + mapping logic as scrape_to_db99.py so the
# two writers stay column-for-column consistent.
from scrape_to_db99 import (
    CATEGORY_MAP,
    LANGUAGE_MAP,
    PATTERN_MAP,
    SCHEDULE_TYPE_MAP,
    get_connection,
    sanitize_display_name,
)
from src.scrapers.discover_mass import enumerate_parishes, parse_parish
from src.utils.cr_markets import norm_city

# ---------------------------------------------------------------------------
# Match thresholds (explicit + tunable)
# ---------------------------------------------------------------------------

# Geo: an existing church within this many METERS of the DiscoverMass lat/lon is
# considered the SAME physical parish. 150 m comfortably covers a church campus
# (building + parking lot + rectory) while excluding the next parish over;
# typical urban parish spacing is >1 km. Two providers geocoding the same
# address rarely differ by more than a city-block (~100 m).
GEO_MATCH_METERS = 150.0

# A geo hit this close is accepted even if the name differs a lot (same address
# == same parish, names get re-spelled/abbreviated all the time).
GEO_STRONG_METERS = 60.0

# When a geo candidate is between GEO_STRONG and GEO_MATCH meters, require the
# normalized names to be reasonably similar; otherwise flag AMBIGUOUS.
NAME_SIM_MIN = 0.55

# Fallback: match on normalized name + city + state (exact-equality on the
# normalized strings) when geo proximity found nothing. But if BOTH sides DO
# have coords and they are farther apart than this, the geo signal contradicts
# the name+city match — flag AMBIGUOUS instead of merging. 5km tolerates a bad
# DiscoverMass geocode within the same city while catching cross-town/cross-
# county same-name parishes.
NAME_CITY_GEO_CONFLICT_METERS = 5000.0

_NAME_STOPWORDS = {
    "the", "of", "our", "lady", "parish", "church", "catholic", "roman",
    "mission", "co", "cathedral", "shrine", "chapel",
}
# "st"/"saint"/"ste"/"sainte" are handled by norm_city's abbreviation expansion.


# ---------------------------------------------------------------------------
# Normalization + similarity helpers
# ---------------------------------------------------------------------------


def _norm_name(name: str) -> str:
    """Normalize a parish name for matching.

    Lowercases, expands St./Ft./Mt. via norm_city's tokenizer, then drops
    generic ecclesial stopwords ("the", "of", "church", "catholic", ...) so
    "St. Mary Catholic Church" and "Saint Marys" collapse to the same token.
    """
    import re

    c = (name or "").lower().strip()
    c = re.sub(r"[.\-',&]", " ", c)
    # Expand abbreviations token-wise using the same map cr_markets uses.
    from src.utils.cr_markets import _ABBR

    toks = [_ABBR.get(t, t) for t in c.split()]
    toks = [t for t in toks if t and t not in _NAME_STOPWORDS]
    return re.sub(r"\s+", "", "".join(toks))


def _name_sim(a: str, b: str) -> float:
    """Cheap similarity in [0,1] between two RAW names via normalized-token
    Jaccard with a containment boost. No external deps."""
    import re

    from src.utils.cr_markets import _ABBR

    def toks(s):
        s = re.sub(r"[.\-',&]", " ", (s or "").lower())
        return {
            _ABBR.get(t, t)
            for t in s.split()
            if t and _ABBR.get(t, t) not in _NAME_STOPWORDS
        }

    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        # Fall back to collapsed-string equality.
        na, nb = _norm_name(a), _norm_name(b)
        if not na or not nb:
            return 0.0
        return 1.0 if na == nb else 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jacc = inter / union if union else 0.0
    # Containment boost: "st mary" vs "st mary star of the sea" should score well.
    contain = inter / min(len(ta), len(tb))
    return max(jacc, 0.5 * jacc + 0.5 * contain)


def _haversine_m(lat1, lon1, lat2, lon2) -> float | None:
    """Great-circle distance in meters, or None if any coord is missing."""
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    except (TypeError, ValueError):
        return None
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _synth_service_id(category_code: str, svc: dict, idx: int) -> str:
    """Stable, deterministic source_service_id for a DiscoverMass service.

    DiscoverMass has no per-service id. We hash category|day|start|end|raw|idx so
    each service row in a parish is unique on (church_id, source_service_id) AND
    a re-scrape of the same unchanged service produces the SAME id (idempotent
    UPSERT, no row churn). `idx` disambiguates two services identical in every
    parsed field. Prefixed "dm-" so it's visibly DiscoverMass-origin and fits the
    varchar(50) column.
    """
    key = "|".join(
        [
            category_code or "",
            svc.get("dayOfWeek") or "",
            svc.get("timeStart") or "",
            svc.get("timeEnd") or "",
            svc.get("timeRaw") or "",
            str(idx),
        ]
    )
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"dm-{h}"


# ---------------------------------------------------------------------------
# Load existing churches for a state (read-only)
# ---------------------------------------------------------------------------


def load_existing_churches(cur, state_code: str) -> list[dict]:
    """Fetch the existing church rows for one state, with the fields we match on."""
    cur.execute(
        """
        SELECT church_id, slug, name, city, state_code, postal_code,
               latitude, longitude, source_url, last_scraped_at
        FROM church
        WHERE state_code = %s
        """,
        (state_code.upper(),),
    )
    rows = cur.fetchall()
    for r in rows:
        r["_name_norm"] = _norm_name(r.get("name") or "")
        r["_city_norm"] = norm_city(r.get("city") or "")
    return rows


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def decide_match(detail: dict, existing: list[dict]) -> dict:
    """Decide MATCH / NEW / AMBIGUOUS for one parsed DiscoverMass parish.

    Returns a decision dict:
        {
          "decision": "MATCH" | "NEW" | "AMBIGUOUS",
          "church_id": int | None,     # set on MATCH
          "reason": str,
          "distance_m": float | None,
          "name_sim": float | None,
          "matched": existing-row dict | None,
          "candidates": [ ... ]        # for AMBIGUOUS, the contenders
        }
    """
    ch = detail.get("church", {})
    dm_lat = ch.get("latitude")
    dm_lon = ch.get("longitude")
    dm_name = ch.get("name") or ""
    dm_city_norm = norm_city(ch.get("city") or "")
    dm_name_norm = _norm_name(dm_name)

    # --- Strategy 1: geo proximity ---
    geo_candidates = []
    if dm_lat is not None and dm_lon is not None:
        for row in existing:
            dist = _haversine_m(dm_lat, dm_lon, row.get("latitude"), row.get("longitude"))
            if dist is not None and dist <= GEO_MATCH_METERS:
                sim = _name_sim(dm_name, row.get("name") or "")
                geo_candidates.append((dist, sim, row))
        geo_candidates.sort(key=lambda t: t[0])

    if geo_candidates:
        dist, sim, row = geo_candidates[0]
        # More than one church inside the geo radius -> let a human look.
        if len(geo_candidates) > 1:
            # Unless the closest is clearly the one (very close AND name matches)
            # and the runner-up is materially farther.
            d2 = geo_candidates[1][0]
            if dist <= GEO_STRONG_METERS and sim >= NAME_SIM_MIN and d2 - dist > 50:
                return {
                    "decision": "MATCH",
                    "church_id": row["church_id"],
                    "reason": f"geo {dist:.0f}m + name_sim {sim:.2f} (clear winner of {len(geo_candidates)})",
                    "distance_m": dist,
                    "name_sim": sim,
                    "matched": row,
                    "candidates": [c[2] for c in geo_candidates],
                }
            return {
                "decision": "AMBIGUOUS",
                "church_id": None,
                "reason": f"{len(geo_candidates)} existing churches within {GEO_MATCH_METERS:.0f}m",
                "distance_m": dist,
                "name_sim": sim,
                "matched": None,
                "candidates": [c[2] for c in geo_candidates],
            }
        # Exactly one geo candidate.
        if dist <= GEO_STRONG_METERS:
            return {
                "decision": "MATCH",
                "church_id": row["church_id"],
                "reason": f"geo {dist:.0f}m (<= {GEO_STRONG_METERS:.0f}m strong)",
                "distance_m": dist,
                "name_sim": sim,
                "matched": row,
                "candidates": [row],
            }
        if sim >= NAME_SIM_MIN:
            return {
                "decision": "MATCH",
                "church_id": row["church_id"],
                "reason": f"geo {dist:.0f}m + name_sim {sim:.2f}",
                "distance_m": dist,
                "name_sim": sim,
                "matched": row,
                "candidates": [row],
            }
        # Geo-close but name very different -> human review.
        return {
            "decision": "AMBIGUOUS",
            "church_id": None,
            "reason": f"geo {dist:.0f}m but name_sim {sim:.2f} < {NAME_SIM_MIN}",
            "distance_m": dist,
            "name_sim": sim,
            "matched": None,
            "candidates": [row],
        }

    # --- Strategy 2: normalized name + city + state fallback ---
    if dm_name_norm and dm_city_norm:
        name_city_hits = [
            row
            for row in existing
            if row["_name_norm"] == dm_name_norm and row["_city_norm"] == dm_city_norm
        ]
        if len(name_city_hits) == 1:
            row = name_city_hits[0]
            dist = _haversine_m(dm_lat, dm_lon, row.get("latitude"), row.get("longitude"))
            # If BOTH sides have coords and they are far apart, geo CONTRADICTS
            # the name+city match (DiscoverMass geocodes are frequently bad, but
            # so is the possibility that these are genuinely different parishes
            # sharing a name in the same city). Flag rather than silently merge.
            if dist is not None and dist > NAME_CITY_GEO_CONFLICT_METERS:
                return {
                    "decision": "AMBIGUOUS",
                    "church_id": None,
                    "reason": (
                        f"name+city match but coords {dist/1000:.1f}km apart "
                        f"(> {NAME_CITY_GEO_CONFLICT_METERS/1000:.0f}km) - geo conflict"
                    ),
                    "distance_m": dist,
                    "name_sim": 1.0,
                    "matched": None,
                    "candidates": [row],
                }
            return {
                "decision": "MATCH",
                "church_id": row["church_id"],
                "reason": (
                    "exact normalized name+city+state"
                    + (f" (coords agree, {dist:.0f}m)" if dist is not None else " (no coords to cross-check)")
                ),
                "distance_m": dist,
                "name_sim": 1.0,
                "matched": row,
                "candidates": [row],
            }
        if len(name_city_hits) > 1:
            return {
                "decision": "AMBIGUOUS",
                "church_id": None,
                "reason": f"{len(name_city_hits)} existing rows share name+city",
                "distance_m": None,
                "name_sim": 1.0,
                "matched": None,
                "candidates": name_city_hits,
            }

    # --- No match ---
    return {
        "decision": "NEW",
        "church_id": None,
        "reason": "no geo candidate within radius, no name+city match",
        "distance_m": None,
        "name_sim": None,
        "matched": None,
        "candidates": [],
    }


# ---------------------------------------------------------------------------
# Build a write-plan row from a parsed parish + a match decision
# ---------------------------------------------------------------------------


def build_church_values(detail: dict, state_code: str, church_id: int | None) -> dict:
    """Map a parsed DiscoverMass detail dict to db99 `church` columns.

    Mirrors scrape_to_db99.upsert_church's mapping. On MATCH we keep the
    existing slug (do NOT overwrite it with the DiscoverMass slug — other tables
    may key on it and the dashboard URL may use it); on NEW we use the
    DiscoverMass slug.
    """
    ch = detail.get("church", {}) or {}
    services_dict = detail.get("services", {}) or {}
    return {
        "church_id": church_id,  # None => INSERT, set => UPDATE that row
        "slug": ch.get("slug") or "",  # only used on INSERT
        "name": ch.get("name") or "",
        "street": ch.get("street") or None,
        "city": ch.get("city") or "",
        "state_code": state_code,
        "postal_code": ch.get("postalCode") or None,
        "latitude": ch.get("latitude"),
        "longitude": ch.get("longitude"),
        "phone": ch.get("phone") or None,
        "website_url": ch.get("website") or None,
        "has_perpetual_adoration": 1 if detail.get("hasPerpetualAdoration") else 0,
        "mass_count": len(services_dict.get("Mass", [])),
        "confession_count": len(services_dict.get("Confession", [])),
        "adoration_count": len(services_dict.get("Adoration", [])),
        "schedule_updated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "source_url": detail.get("source_url"),
        "last_scraped_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_service_values(detail: dict) -> list[dict]:
    """Map parsed services to db99 `service` column dicts (mirrors scrape_to_db99)."""
    out = []
    services_dict = detail.get("services", {}) or {}
    for category_name, service_list in services_dict.items():
        for idx, svc in enumerate(service_list):
            category_code = CATEGORY_MAP.get(category_name, "other")
            schedule_type = SCHEDULE_TYPE_MAP.get(svc.get("scheduleType", ""), "other")
            lang_raw = svc.get("language")
            language_code = LANGUAGE_MAP.get(lang_raw, None) if lang_raw else None
            pattern_raw = svc.get("pattern")
            pattern_code = PATTERN_MAP.get(pattern_raw, pattern_raw) if pattern_raw else None
            out.append(
                {
                    "source_service_id": _synth_service_id(category_code, svc, idx),
                    "category_code": category_code,
                    "schedule_type_code": schedule_type,
                    "day_code": svc.get("dayOfWeek"),
                    "time_start": svc.get("timeStart"),
                    "time_end": svc.get("timeEnd"),
                    "event_date": svc.get("eventDate"),
                    "language_code": language_code,
                    "pattern_code": pattern_code,
                    "relation_code": svc.get("timeRelation"),
                    "reference_service": svc.get("referenceService"),
                    "offset_minutes": svc.get("offsetMinutes"),
                    "location": svc.get("location"),
                    "display_name": sanitize_display_name(
                        svc.get("displayName", category_name), category_code
                    ),
                    "notes_raw": svc.get("notes"),
                }
            )
    return out


# ---------------------------------------------------------------------------
# COMMIT path (implemented but NOT called in dry-run / Phase 2)
# ---------------------------------------------------------------------------


def _commit_plan(cur, plan_item: dict) -> None:
    """Write ONE planned parish (church + services) to db99.

    INVOKED ONLY when --commit is passed. Phase 2 never calls this.

    MATCH  -> UPDATE existing church_id in place (preserve church_id + slug +
              bulletin links), then UPSERT its services.
    NEW    -> INSERT a new church (DiscoverMass slug), then INSERT its services.
    AMBIGUOUS -> skipped (left for human review).
    """
    decision = plan_item["decision"]
    if decision == "AMBIGUOUS":
        return

    cv = plan_item["church_values"]
    services = plan_item["service_values"]

    if decision == "MATCH":
        church_id = cv["church_id"]
        # Refresh mass-times fields in place. Deliberately NOT touching slug.
        cur.execute(
            """
            UPDATE church SET
                name=%s, street=%s, city=%s, postal_code=%s,
                latitude=%s, longitude=%s, phone=%s, website_url=%s,
                has_perpetual_adoration=%s,
                mass_count=%s, confession_count=%s, adoration_count=%s,
                schedule_updated_at=%s, source_url=%s, last_scraped_at=%s
            WHERE church_id=%s
            """,
            (
                cv["name"], cv["street"], cv["city"], cv["postal_code"],
                cv["latitude"], cv["longitude"], cv["phone"], cv["website_url"],
                cv["has_perpetual_adoration"],
                cv["mass_count"], cv["confession_count"], cv["adoration_count"],
                cv["schedule_updated_at"], cv["source_url"], cv["last_scraped_at"],
                church_id,
            ),
        )
    else:  # NEW
        cur.execute(
            """
            INSERT INTO church (
                slug, name, street, city, state_code, postal_code,
                latitude, longitude, phone, website_url,
                has_perpetual_adoration,
                mass_count, confession_count, adoration_count,
                schedule_updated_at, source_url, last_scraped_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), street=VALUES(street), city=VALUES(city),
                postal_code=VALUES(postal_code), latitude=VALUES(latitude),
                longitude=VALUES(longitude), phone=VALUES(phone),
                website_url=VALUES(website_url),
                has_perpetual_adoration=VALUES(has_perpetual_adoration),
                mass_count=VALUES(mass_count),
                confession_count=VALUES(confession_count),
                adoration_count=VALUES(adoration_count),
                schedule_updated_at=VALUES(schedule_updated_at),
                source_url=VALUES(source_url), last_scraped_at=VALUES(last_scraped_at)
            """,
            (
                cv["slug"], cv["name"], cv["street"], cv["city"], cv["state_code"],
                cv["postal_code"], cv["latitude"], cv["longitude"], cv["phone"],
                cv["website_url"], cv["has_perpetual_adoration"],
                cv["mass_count"], cv["confession_count"], cv["adoration_count"],
                cv["schedule_updated_at"], cv["source_url"], cv["last_scraped_at"],
            ),
        )
        cur.execute("SELECT church_id FROM church WHERE slug=%s", (cv["slug"],))
        row = cur.fetchone()
        if not row:
            return
        church_id = row["church_id"]

    # Replace this church's schedule cleanly ONLY when DiscoverMass actually has a
    # replacement (>=1 service): retire ALL existing services first (stale
    # CatholicIndex rows + any prior DiscoverMass rows), then the current DiscoverMass
    # services below re-activate the set. Makes re-runs an idempotent replace and
    # drops mass times that no longer exist at the source. If DM found 0 services
    # (institution/convent or a parse miss), we KEEP the existing schedule rather
    # than blanking the church. Without the replace, fresh `dm-` services would
    # accumulate alongside the frozen CatholicIndex rows and the dashboard would
    # show both.
    if services:
        cur.execute("UPDATE service SET is_active=0 WHERE church_id=%s", (church_id,))

    for sv in services:
        if not sv.get("source_service_id"):
            continue
        cur.execute(
            """
            INSERT INTO service (
                church_id, source_service_id, category_code,
                schedule_type_code, day_code, time_start, time_end,
                event_date, language_code, pattern_code,
                relation_code, reference_service, offset_minutes,
                location, display_name, notes_raw, is_active
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            ON DUPLICATE KEY UPDATE
                category_code=VALUES(category_code),
                schedule_type_code=VALUES(schedule_type_code),
                day_code=VALUES(day_code),
                time_start=VALUES(time_start), time_end=VALUES(time_end),
                event_date=VALUES(event_date), language_code=VALUES(language_code),
                pattern_code=VALUES(pattern_code), relation_code=VALUES(relation_code),
                reference_service=VALUES(reference_service),
                offset_minutes=VALUES(offset_minutes),
                location=VALUES(location), display_name=VALUES(display_name),
                notes_raw=VALUES(notes_raw), is_active=1
            """,
            (
                church_id, sv["source_service_id"], sv["category_code"],
                sv["schedule_type_code"], sv["day_code"], sv["time_start"],
                sv["time_end"], sv["event_date"], sv["language_code"],
                sv["pattern_code"], sv["relation_code"], sv["reference_service"],
                sv["offset_minutes"], sv["location"], sv["display_name"],
                sv["notes_raw"],
            ),
        )


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def _fmt_coord(lat, lon) -> str:
    if lat is None or lon is None:
        return "(no coords)"
    try:
        return f"({float(lat):.4f},{float(lon):.4f})"
    except (TypeError, ValueError):
        return "(no coords)"


def print_report(state_code, stats, plan, samples_n=10):
    line = "=" * 72
    print("\n" + line)
    print(f"  DiscoverMass -> db99  DRY-RUN REPORT  (state={state_code})")
    print(line)
    print("  NO DATABASE WRITES WERE PERFORMED.\n")
    print(f"  parishes enumerated ........ {stats['enumerated']}")
    print(f"  skipped (fresh, stale-only)  {stats.get('skipped_fresh', 0)}")
    print(f"  parsed OK .................. {stats['parsed_ok']}")
    print(f"  parse failures ............. {stats['parse_fail']}")
    print(f"  -> matched to existing ..... {stats['match']}")
    print(f"  -> new insert .............. {stats['new']}")
    print(f"  -> ambiguous (flagged) ..... {stats['ambiguous']}")
    print()
    print(f"  existing {state_code} church rows ...... {stats['existing_rows']}")
    print(f"  would be REFRESHED (update). {stats['match']}  (distinct: {stats['distinct_matched']})")
    print(f"  net-new parishes (insert) .. {stats['new']}")
    print(line)

    # Sample MATCH/NEW/AMBIGUOUS decisions.
    print("\n  SAMPLE DECISIONS")
    print("  " + "-" * 68)
    shown = 0
    # Prefer to show a mix: some MATCH, some NEW, all AMBIGUOUS.
    ordered = (
        [p for p in plan if p["decision"] == "MATCH"][: samples_n - 2]
        + [p for p in plan if p["decision"] == "NEW"][:2]
        + [p for p in plan if p["decision"] == "AMBIGUOUS"]
    )
    for p in ordered[: max(samples_n, stats["ambiguous"] + 2)]:
        d = p["decision"]
        dm = p["dm"]
        dm_name = dm.get("name") or p["slug"]
        dm_city = dm.get("city") or "?"
        dm_co = _fmt_coord(dm.get("latitude"), dm.get("longitude"))
        if d == "MATCH":
            m = p["matched"]
            dist = p["distance_m"]
            dist_s = f"{dist:.0f}m" if dist is not None else "n/a"
            print(f"  [MATCH] {dm_name} / {dm_city} {dm_co}")
            print(f"          <-> #{m['church_id']} {m.get('name')} / {m.get('city')} "
                  f"{_fmt_coord(m.get('latitude'), m.get('longitude'))}")
            print(f"          dist={dist_s} name_sim={p['name_sim']:.2f}  [{p['reason']}]")
        elif d == "NEW":
            print(f"  [NEW]   {dm_name} / {dm_city} {dm_co}  slug={p['slug']}")
            print(f"          {p['reason']}")
        else:
            print(f"  [AMBIG] {dm_name} / {dm_city} {dm_co}  -- {p['reason']}")
            for c in p.get("candidates", [])[:4]:
                print(f"          candidate #{c['church_id']} {c.get('name')} / {c.get('city')} "
                      f"{_fmt_coord(c.get('latitude'), c.get('longitude'))}")
        shown += 1
    if shown == 0:
        print("  (no decisions)")
    print(line)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="MATCH-OR-INSERT DiscoverMass parishes into db99 (dry-run by default)"
    )
    parser.add_argument("--state", type=str, required=True, help="2-letter state code (e.g. AR)")
    parser.add_argument("--limit", type=int, default=0, help="Cap parishes (0=all)")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="ACTUALLY WRITE to db99. Phase 2 must NOT use this.",
    )
    parser.add_argument("--samples", type=int, default=10, help="Sample decisions to print")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=0,
        help="Skip parishes whose matching church row was scraped within the last N days "
        "(0=disabled, re-scrape everything). Net-new parishes are always parsed.",
    )
    args = parser.parse_args()

    state_code = args.state.upper()
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[OK] DiscoverMass -> db99  state={state_code}  mode={mode}")

    conn = get_connection()
    cur = conn.cursor()

    existing = load_existing_churches(cur, state_code)
    print(f"[OK] loaded {len(existing)} existing {state_code} church rows (read-only)")

    slugs = enumerate_parishes(state=state_code, limit=args.limit)
    print(f"[OK] enumerated {len(slugs)} DiscoverMass parishes")

    # Stale-only refresh: skip parishes whose matching church row was scraped
    # within the last N days. Keyed on slug (DiscoverMass-inserted churches store
    # their DM slug), so net-new parishes — those with no existing slug — are never
    # skipped. This makes each sharded run cheap and self-healing without redoing
    # churches already refreshed this cycle.
    fresh_slugs: set[str] = set()
    if args.stale_days > 0:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=args.stale_days)
        for r in existing:
            ls = r.get("last_scraped_at")
            if r.get("slug") and ls is not None and ls >= cutoff:
                fresh_slugs.add(r["slug"])
        print(f"[OK] stale-days={args.stale_days}: {len(fresh_slugs)} fresh churches will be skipped")

    stats = {
        "enumerated": len(slugs),
        "skipped_fresh": 0,
        "parsed_ok": 0,
        "parse_fail": 0,
        "match": 0,
        "new": 0,
        "ambiguous": 0,
        "existing_rows": len(existing),
    }
    plan = []
    matched_ids = set()

    for i, slug in enumerate(slugs):
        if slug in fresh_slugs:
            stats["skipped_fresh"] += 1
            continue
        try:
            detail = parse_parish(slug)
        except Exception as e:  # noqa: BLE001
            detail = None
            print(f"  [ERR] parse exception {slug}: {e}")
        if not detail:
            stats["parse_fail"] += 1
            continue
        stats["parsed_ok"] += 1

        decision = decide_match(detail, existing)
        d = decision["decision"]
        if d == "MATCH":
            stats["match"] += 1
            matched_ids.add(decision["church_id"])
        elif d == "NEW":
            stats["new"] += 1
        else:
            stats["ambiguous"] += 1

        plan.append(
            {
                "slug": slug,
                "decision": d,
                "reason": decision["reason"],
                "distance_m": decision["distance_m"],
                "name_sim": decision["name_sim"],
                "matched": decision["matched"],
                "candidates": decision["candidates"],
                "church_id": decision["church_id"],
                "dm": detail.get("church", {}),
                "church_values": build_church_values(detail, state_code, decision["church_id"]),
                "service_values": build_service_values(detail),
            }
        )

        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(slugs)}] parsed_ok={stats['parsed_ok']} "
                  f"match={stats['match']} new={stats['new']} ambig={stats['ambiguous']}")

    stats["distinct_matched"] = len(matched_ids)

    if args.commit:
        # Guarded write path — Phase 2 will not reach here.
        print("[OK] --commit set: writing plan to db99 ...")
        written = 0
        for p in plan:
            if p["decision"] == "AMBIGUOUS":
                continue
            _commit_plan(cur, p)
            written += 1
        # Invariant cleanup: for any church in this state now served by DiscoverMass
        # (>=1 active dm- service), retire its still-active stale CatholicIndex
        # services. Keeps data consistent even if a run only reached some parishes
        # (e.g. proxy throttling). Churches DM gave 0 services keep their old schedule.
        cur.execute(
            """
            UPDATE service s
            JOIN (SELECT DISTINCT church_id FROM service
                  WHERE is_active=1 AND source_service_id LIKE 'dm-%%') dm
                 ON dm.church_id = s.church_id
            JOIN church c ON c.church_id = s.church_id
            SET s.is_active = 0
            WHERE c.state_code = %s AND s.is_active = 1
              AND s.source_service_id NOT LIKE 'dm-%%'
            """,
            (state_code,),
        )
        retired = cur.rowcount
        conn.commit()  # explicit — do not rely on implicit/autocommit
        print(f"[OK] wrote {written} parishes (skipped {stats['ambiguous']} ambiguous); "
              f"retired {retired} stale services; committed")
    else:
        print("[OK] dry-run: building report, NO writes performed")

    print_report(state_code, stats, plan, samples_n=args.samples)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
