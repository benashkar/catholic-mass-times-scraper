"""ELCA congregation directory — the whole of a state in one GET.

`www.elca.org/directory/congregations` looks like a dead end: the page is
Webflow and the congregation data is nowhere in its JS chunks. It is rendered by
**Wized**, whose config (`embed.wized.com/zW6FMfNuLX2zDTZdpNu2.js`) names a
**Xano** backend and the request that fills the table. That backend is public --
no key, no cookie, no token -- and returns an entire state as one JSON array
with no pagination.

    GET {BASE}/api/get_congregations?LocationState=Wisconsin   -> 624 records

The parameter takes the **full state name**. Passing "WI" returns `[]`, which is
almost certainly what led an earlier probe to conclude the directory served no
data at all.

Why this source is worth having over a generic roster: it carries
`LocationURL`, the congregation's OWN website (513 of 624 in Wisconsin), and
`website_url` is what drives the entire bulletin pipeline. A roster without
websites yields no bulletins. It also carries lat/lon for every single record,
which feeds the existing <=150m dedup directly, and the synod, which gives
denomination for free.

It does NOT carry worship times. Those still have to come from the
congregation's own site.

Read-only; this module fetches and parses, and writes nothing.
"""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://x27u-79is-eddy.n7c.xano.io/api:tzLVgPph"

# Identify honestly. Never disguise the client -- the standing rule on this
# project is that a scraper says who it is.
UA = "church-scrapes/1.0 (+ben.ashkar@locallabs.com)"

# LocationState wants the full name. Keeping the mapping here rather than
# pulling a dependency for it, because the failure mode -- a silent empty array
# that looks exactly like "this state has no congregations" -- is expensive.
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def _get(url, timeout=120, retries=3):
    """Fetch JSON. urllib rather than requests/curl.

    curl exits 60 on hosts with an incomplete TLS chain and the documented
    reflex on this project is to reach for urllib rather than disable
    verification, so this keeps that habit even though this particular host
    presents a complete chain today.
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ELCA fetch failed after {retries} tries: {last}")


def enumerate_state(state_code):
    """Every ELCA congregation in one state, as raw API records."""
    name = STATE_NAMES.get((state_code or "").upper())
    if not name:
        raise ValueError(f"unknown state code {state_code!r}")
    url = (f"{BASE}/api/get_congregations?"
           + urllib.parse.urlencode({"LocationState": name}))
    rows = _get(url)
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected payload for {state_code}: {type(rows)}")
    return rows


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _website(rec):
    """Normalise LocationURL into something fetchable, or None.

    The field is entered by congregations and arrives in every shape a human
    can produce: bare domains, a stray 'http//', trailing whitespace, and the
    occasional Facebook page. A bare domain with no scheme is exactly the
    unfetchable state that stranded hundreds of Catholic parishes in
    website_url, so it is fixed here rather than at the far end.
    """
    u = _clean(rec.get("LocationURL"))
    if not u:
        return None
    u = u.replace("http//", "http://").replace("https//", "https://")
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    if " " in u.strip():
        u = u.strip().split()[0]
    return u


def parse_congregation(rec):
    """One API record -> the repo's church/services shape.

    Mirrors what the DiscoverMass adapter returns so the existing loader
    conventions (decide_match, synthesised source ids) apply unchanged.
    `services` is always empty: the ELCA API carries no worship times, and
    inventing a Sunday 10am would be a confident lie of exactly the kind the
    NULL-beats-bad-data rule exists to prevent.
    """
    lat, lon = rec.get("Latitude"), rec.get("Longitude")
    try:
        lat = float(lat) if lat not in (None, "") else None
        lon = float(lon) if lon not in (None, "") else None
    except (TypeError, ValueError):
        lat = lon = None

    zip_raw = _clean(rec.get("LocationZIP")) or ""
    return {
        "church": {
            "source_id": _clean(rec.get("LocationID")),
            "name": _clean(rec.get("LocationName")),
            "street": _clean(rec.get("LocationStreetAddress")),
            "city": _clean(rec.get("LocationCity")),
            "state_code": None,  # filled by the caller; API gives full name
            "postal_code": zip_raw,
            "postal_code_clean": zip_raw.split("-")[0] or None,
            "latitude": lat,
            "longitude": lon,
            "phone": _clean(rec.get("Phone")),
            "email": _clean(rec.get("Email")),
            "website_url": _website(rec),
            "denomination": "elca",
            "source_provider": "elca",
            "synod": _clean(rec.get("SynodName")),
        },
        "services": {},
    }


def parse_state(state_code):
    """[parsed] for a whole state, with state_code stamped on each record."""
    out = []
    for rec in enumerate_state(state_code):
        p = parse_congregation(rec)
        p["church"]["state_code"] = state_code.upper()
        if p["church"]["name"]:
            out.append(p)
    return out
