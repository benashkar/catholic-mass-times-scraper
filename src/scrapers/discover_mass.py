"""
discover_mass.py

Scraper for DiscoverMass.com — the new PRIMARY mass-times data source (Tier 1),
replacing CatholicIndex.org which went behind a hard Cloudflare Managed Challenge
in ~April 2026.

WHY DISCOVERMASS:
    CatholicIndex is dead from our IPs (100% of churches >30d stale). DiscoverMass
    is wide open: plain `requests` + a Chrome User-Agent returns HTTP 200, no
    Cloudflare, no proxy needed. robots.txt only sets `Crawl-delay: 10`.

WHAT THIS MODULE DOES:
    1. ENUMERATION — list every parish on the site, optionally filtered by state,
       via the WordPress core XML sitemap (the Javo `item` custom-post-type is
       split across `wp-sitemap-posts-item-{1..N}.xml`). ~20,300 parishes total.
    2. PARSING — `parse_parish(slug_or_url)` fetches a `/church/{slug}/` detail
       page and returns a dict whose shape MIRRORS
       `catholic_index.scrape_church_detail()` so Phase-2 pipeline integration is
       a drop-in (same `church` + `services` keys, same per-service fields).

SITE STRUCTURE (DiscoverMass = Javo Directory WordPress theme):
    Parish detail page:  https://discovermass.com/church/{slug}/
        slug = church-name-city-state  (lowercase, hyphenated, 2-letter state)
        - <h1 class="uppercase">       -> parish name
        - <h2 id="theaddress">         -> "Street, City, ST ZIP"
        - LatLng("lat", "lng")          -> coordinates (inline JS)
        - href="tel:..."                -> phone
        - "Visit Website" anchor        -> parish's own website
        - Mass Times block:  <ul><li><h5>Mass Times</h5></li>
            <li><span class="label">Saturday</span>
                <span class='serviceTime'><span class='time'>4:00pm</span></span>...
        - Other Services block: <li class="Adoration">/<li class="Confessions">
        - A boilerplate COVID-19 notice (id="covid") that ALSO says "Mass Times" —
          we explicitly skip it and parse the real day-labeled rows.

    State city list:  https://discovermass.com/list/?state=xx
        - renders `data-location='City, ST'` anchors but the per-city -> parish
          link is a JS geo-search AJAX (lat/lng bounds) that is intractable to
          reverse-engineer. We DO NOT use this for enumeration; the sitemap is
          authoritative and complete. (See module docstring note in
          enumerate_state_cities for the city-list helper, kept for reference.)

USAGE:
    from src.scrapers.discover_mass import enumerate_parishes, parse_parish

    # Every parish in Arkansas (list of /church/{slug}/ slugs)
    slugs = enumerate_parishes(state="AR")

    # Full schedule for one parish (mirrors catholic_index detail shape)
    detail = parse_parish("st-mary-batesville-ar")
    for mass in detail["services"]["Mass"]:
        print(mass["dayOfWeek"], mass["timeStart"], mass["displayName"])
"""

import re
from datetime import UTC, datetime

from config.settings import DISCOVER_MASS_BASE_URL
from src.utils.http import fetch_page
from src.utils.logger import get_logger

logger = get_logger(__name__)

# The Javo parish custom-post-type is exported as the `item` post type in the
# WordPress core sitemap, split across numbered sub-sitemaps. As of 2026-06 there
# are 11 (10 full pages of 2000 + 1 partial). We probe sequentially until a
# sub-sitemap 404s, so this auto-adjusts as the site grows.
SITEMAP_ITEM_URL = DISCOVER_MASS_BASE_URL + "/wp-sitemap-posts-item-{n}.xml"

# Map day labels DiscoverMass uses -> the 3-letter dayOfWeek codes the existing
# CatholicIndex pipeline emits (see rsc_extractor.extract_church_detail docstring).
_DAY_CODE = {
    "sunday": "Sun",
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sun": "Sun",
    "mon": "Mon",
    "tue": "Tue",
    "tues": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "thur": "Thu",
    "thurs": "Thu",
    "fri": "Fri",
    "sat": "Sat",
}

# scheduleType bucket (mirrors catholic_index values) keyed by dayOfWeek code.
_SCHEDULE_TYPE = {
    "Sun": "sunday",
    "Sat": "saturday",
    "Mon": "specific_weekday",
    "Tue": "specific_weekday",
    "Wed": "specific_weekday",
    "Thu": "specific_weekday",
    "Fri": "specific_weekday",
}


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def _state_from_slug(slug: str) -> str:
    """Return the 2-letter state suffix of a parish slug, or "" if absent.

    Slugs end in `-{city}-{state}`, e.g. "st-mary-batesville-ar" -> "ar".
    A small minority (~1%) have no clean 2-letter suffix; those return "".
    """
    m = re.search(r"-([a-z]{2})$", slug or "")
    return m.group(1) if m else ""


def enumerate_parishes(state: str = "", limit: int = 0, max_sitemaps: int = 30) -> list[str]:
    """List parish slugs from DiscoverMass via the WordPress XML sitemap.

    This is the reliable, authoritative enumeration mechanism: the WP core
    sitemap exports every Javo `item` (parish) as a `/church/{slug}/` <loc>,
    split across `wp-sitemap-posts-item-{n}.xml`. We walk them until one 404s.

    Args:
        state: Optional 2-letter state code (case-insensitive, e.g. "AR").
               When given, only slugs whose `-{state}` suffix matches are
               returned. When empty, every parish on the site is returned.
        limit: Optional cap on the number of slugs returned (0 = no cap).
               Applied after state filtering. Useful for test runs.
        max_sitemaps: Safety bound on how many sub-sitemaps to probe.

    Returns:
        A list of parish slugs (NOT full URLs), e.g.
        ["st-mary-batesville-ar", "st-andrew-danville-ar", ...].
        Empty list if the sitemap could not be fetched.
    """
    state_lc = (state or "").strip().lower()
    slugs: list[str] = []

    for n in range(1, max_sitemaps + 1):
        url = SITEMAP_ITEM_URL.format(n=n)
        xml = fetch_page(url)
        if not xml:
            # First sub-sitemap missing => something is wrong; otherwise we've
            # walked past the last page and are done.
            if n == 1:
                logger.error(f"[ERR] Could not fetch parish sitemap: {url}")
            else:
                logger.debug(f"No sitemap at {url}; assuming end of parish list")
            break

        page_slugs = re.findall(
            r"<loc>" + re.escape(DISCOVER_MASS_BASE_URL) + r"/church/([^<]+?)/</loc>", xml
        )
        if not page_slugs:
            logger.debug(f"Sitemap {url} had 0 parish <loc> entries; stopping")
            break

        for sl in page_slugs:
            if state_lc and _state_from_slug(sl) != state_lc:
                continue
            slugs.append(sl)

        logger.info(
            f"[OK] sitemap item-{n}: {len(page_slugs)} parishes "
            f"(running total kept: {len(slugs)})"
        )

        if limit and len(slugs) >= limit:
            slugs = slugs[:limit]
            break

    logger.info(
        f"[OK] enumerated {len(slugs)} parishes"
        + (f" for state={state_lc.upper()}" if state_lc else " (all states)")
    )
    return slugs


def enumerate_state_cities(state: str) -> list[str]:
    """Return the city names DiscoverMass lists for a state (reference helper).

    Fetches https://discovermass.com/list/?state=xx and harvests the
    `data-location='City, ST'` anchors. This is NOT used for parish enumeration
    (the per-city -> parish link is a JS geo-bounds AJAX we deliberately do not
    reverse-engineer); the sitemap is the enumeration source of truth. This
    helper is kept for coverage cross-checks and per-state sharding hints.

    Args:
        state: 2-letter state code (case-insensitive).

    Returns:
        List of "City, ST" strings, or [] on failure.
    """
    url = f"{DISCOVER_MASS_BASE_URL}/list/?state={(state or '').strip().lower()}"
    html = fetch_page(url)
    if not html:
        logger.error(f"[ERR] Could not fetch state city list: {url}")
        return []
    cities = re.findall(r"data-location='([^']+)'", html)
    logger.info(f"[OK] {len(cities)} cities listed for {state.upper()}")
    return cities


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _slug_to_url(slug_or_url: str) -> tuple[str, str]:
    """Normalize a slug or full URL into (slug, full_url)."""
    s = (slug_or_url or "").strip()
    if s.startswith("http"):
        m = re.search(r"/church/([^/?#]+)", s)
        slug = m.group(1) if m else s
        return slug, f"{DISCOVER_MASS_BASE_URL}/church/{slug}/"
    slug = s.strip("/")
    return slug, f"{DISCOVER_MASS_BASE_URL}/church/{slug}/"


def _parse_address(addr: str) -> dict:
    """Split "3800 Harrison St., Batesville, AR 72501" into parts.

    Returns dict with street, city, stateRegion (full-ish 2-letter), postalCode.
    Degrades gracefully (missing parts -> "").
    """
    out = {"street": "", "city": "", "stateRegion": "", "postalCode": ""}
    addr = (addr or "").strip()
    if not addr:
        return out
    # Trailing "ST ZIP" (ZIP may be 5 or 5-4)
    m = re.search(r",?\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", addr)
    if m:
        out["stateRegion"] = m.group(1)
        out["postalCode"] = m.group(2)
        addr = addr[: m.start()].rstrip().rstrip(",")
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 2:
        out["city"] = parts[-1]
        out["street"] = ", ".join(parts[:-1])
    elif len(parts) == 1:
        # No comma — could be just a city or just a street.
        out["street"] = parts[0]
    return out


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _split_time_range(time_str: str) -> tuple[str, str]:
    """"4:00pm" -> ("16:00:00", None-as-""); "6:30pm-8:00am" -> start,end (24h)."""
    time_str = (time_str or "").strip()
    if not time_str:
        return "", ""
    if "-" in time_str:
        a, b = time_str.split("-", 1)
        return _to_24h(a), _to_24h(b)
    return _to_24h(time_str), ""


def _to_24h(t: str) -> str:
    """"4:00pm" / "10:30am" / "8am" -> "HH:MM:SS" (24h). "" if unparseable."""
    t = (t or "").strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", t)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}:00"


def _parse_service_li(li_html: str) -> list[dict]:
    """Parse one <li> from a Mass Times / Other Services block.

    The <li> has one `<span class="label">DAY</span>` followed by one or more
    `<span class='serviceTime'>...<span class='time'>TIME</span>...
    [<span class='language'>(Lang)</span>] [- <span class='comment'>note</span>]
    </span>`. For Other Services, the label is the category (Adoration /
    Confessions) and each serviceTime may be prefixed with "Tue:" etc.

    Returns a list of normalized service-entry dicts.
    """
    label = ""
    lm = re.search(r"<span class=[\"']label[\"']>(.*?)</span>", li_html, re.DOTALL)
    if lm:
        label = _strip_tags(lm.group(1))

    entries = []
    # Each serviceTime span (capture inner up to the closing span balanced enough
    # for this flat markup).
    for sm in re.finditer(
        r"<span class=['\"]serviceTime['\"]>(.*?)</span>\s*(?:,|$|</li>|<span class=['\"]serviceTime)",
        li_html + "</li>",
        re.DOTALL,
    ):
        block = sm.group(1)
        # An inline day prefix like "Tue:" (used in Other Services rows)
        day_prefix = ""
        dpm = re.match(r"\s*([A-Za-z]{3,9})\s*:", block)
        if dpm:
            day_prefix = dpm.group(1)

        time_m = re.search(r"<span class=['\"]time['\"]>(.*?)</span>", block, re.DOTALL)
        time_raw = _strip_tags(time_m.group(1)) if time_m else ""
        lang_m = re.search(r"<span class=['\"]language['\"]>\(?(.*?)\)?</span>", block, re.DOTALL)
        language = _strip_tags(lang_m.group(1)) if lang_m else ""
        note_m = re.search(r"<span class=['\"]comment['\"]>(.*?)</span>", block, re.DOTALL)
        notes = _strip_tags(note_m.group(1)) if note_m else ""

        if not time_raw and not notes:
            continue

        day_label = (day_prefix or label).strip()
        day_code = _DAY_CODE.get(day_label.lower(), "")
        start, end = _split_time_range(time_raw)

        entries.append(
            {
                "dayLabel": day_label,
                "dayOfWeek": day_code,
                "timeStart": start,
                "timeEnd": end,
                "timeRaw": time_raw,
                "language": language or None,
                "notes": notes or None,
            }
        )
    return entries


def _extract_block(html: str, heading: str) -> str:
    """Return the <ul>...</ul> whose first <li> contains <h5>heading</h5>.

    Skips the COVID-19 notice (which is not a <ul> of day rows).
    """
    # Find the <h5>heading</h5>, then expand to the enclosing <ul>...</ul>.
    hm = re.search(r"<h5>\s*" + re.escape(heading) + r"\s*</h5>", html, re.IGNORECASE)
    if not hm:
        return ""
    ul_start = html.rfind("<ul", 0, hm.start())
    if ul_start == -1:
        return ""
    ul_end = html.find("</ul>", hm.end())
    if ul_end == -1:
        return ""
    return html[ul_start : ul_end + len("</ul>")]


def parse_parish(slug_or_url: str) -> dict | None:
    """Scrape and parse one DiscoverMass parish detail page.

    Returns a dict whose shape mirrors catholic_index.scrape_church_detail():

        {
          "church": {
            "slug", "name", "street", "city", "stateRegion", "state",
            "postalCode", "latitude", "longitude", "phone", "website",
          },
          "services": {
            "Mass": [ {serviceId, category, scheduleType, dayOfWeek, dayLabel,
                       timeStart, timeEnd, timeRaw, displayName, language,
                       notes}, ... ],
            "Confession": [ ... ],
            "Adoration": [ ... ],
            "Devotions": [ ... ],
          },
          "massCount", "confessionCount", "adorationCount",
          "hasPerpetualAdoration",
          "totalServices",
          "source_url", "last_scraped",
        }

    Returns None if the page could not be fetched.
    Uses `dict.get(k) or ""` style None-safety throughout. Logs with [OK]/[ERR].
    """
    slug, url = _slug_to_url(slug_or_url)
    logger.info(f"Parsing DiscoverMass parish: {slug}")

    html = fetch_page(url)
    if not html:
        logger.error(f"[ERR] Could not fetch parish page: {slug}")
        return None

    # --- Identity / contact ---
    name_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    name = _strip_tags(name_m.group(1)) if name_m else ""

    addr_m = re.search(r'<h2 id=["\']theaddress["\'][^>]*>(.*?)</h2>', html, re.DOTALL)
    addr_str = _strip_tags(addr_m.group(1)) if addr_m else ""
    addr = _parse_address(addr_str)

    geo_m = re.search(r'LatLng\(\s*["\']([\-\d.]+)["\']\s*,\s*["\']([\-\d.]+)["\']', html)
    latitude = float(geo_m.group(1)) if geo_m else None
    longitude = float(geo_m.group(2)) if geo_m else None

    tel_m = re.search(r'href=["\']tel:([^"\']+)["\']', html)
    phone = (tel_m.group(1) or "").strip() if tel_m else ""

    # Parish's own website: prefer the "Visit Website" anchor, fall back to the
    # "parish website" link in the COVID notice.
    web_m = re.search(
        r'<a href=["\']([^"\']+)["\'][^>]*>\s*Visit Website\s*</a>', html, re.IGNORECASE
    )
    if not web_m:
        web_m = re.search(
            r'<a href=["\']([^"\']+)["\'][^>]*>\s*parish website\s*</a>', html, re.IGNORECASE
        )
    website = (web_m.group(1) or "").strip() if web_m else ""

    # --- Service blocks ---
    services = {"Mass": [], "Confession": [], "Adoration": [], "Devotions": []}

    # Mass Times block: every <li> with a day label is a mass row.
    mass_ul = _extract_block(html, "Mass Times")
    if mass_ul:
        for li in re.findall(r"<li[^>]*>(.*?)</li>", mass_ul, re.DOTALL):
            if "<h5>" in li:  # the heading <li>
                continue
            for e in _parse_service_li(li):
                e2 = _to_service(e, "Mass")
                services["Mass"].append(e2)

    # Other Services block: <li class="Adoration"> / <li class="Confessions"> /
    # <li class="Devotions">. The <li>'s class names the category.
    other_ul = _extract_block(html, "Other Services")
    if other_ul:
        for cls, body in re.findall(
            r'<li class=["\']([A-Za-z]+)["\']>(.*?)</li>', other_ul, re.DOTALL
        ):
            category = _normalize_category(cls)
            if category not in services:
                category = "Devotions"
            for e in _parse_service_li(body):
                services[category].append(_to_service(e, category))

    mass_count = len(services["Mass"])
    confession_count = len(services["Confession"])
    adoration_count = len(services["Adoration"])

    # Perpetual adoration: a note/comment mentioning "perpetual".
    has_perpetual = any(
        "perpetual" in ((s.get("notes") or "") + (s.get("displayName") or "")).lower()
        for s in services["Adoration"]
    )

    detail = {
        "church": {
            "slug": slug,
            "name": name,
            "street": addr.get("street") or "",
            "city": addr.get("city") or "",
            "stateRegion": addr.get("stateRegion") or "",
            "state": addr.get("stateRegion") or _state_from_slug(slug).upper(),
            "postalCode": addr.get("postalCode") or "",
            "latitude": latitude,
            "longitude": longitude,
            "phone": phone or "",
            "website": website or "",
        },
        "services": services,
        "massCount": mass_count,
        "confessionCount": confession_count,
        "adorationCount": adoration_count,
        "hasPerpetualAdoration": has_perpetual,
        "totalServices": mass_count + confession_count + adoration_count + len(services["Devotions"]),
        "source_url": url,
        "last_scraped": datetime.now(UTC).isoformat(),
    }

    logger.info(
        f"[OK] parsed {name or slug}: "
        f"{mass_count} mass / {confession_count} confession / {adoration_count} adoration"
    )
    return detail


def _normalize_category(cls: str) -> str:
    c = (cls or "").lower()
    if c.startswith("confession"):
        return "Confession"
    if c.startswith("adoration"):
        return "Adoration"
    if c.startswith("mass"):
        return "Mass"
    return "Devotions"


def _to_service(entry: dict, category: str) -> dict:
    """Convert a raw _parse_service_li entry into the pipeline service shape."""
    day = entry.get("dayOfWeek") or ""
    return {
        "serviceId": "",  # DiscoverMass has no per-service id
        "category": category,
        "scheduleType": _SCHEDULE_TYPE.get(day, "other"),
        "dayOfWeek": day or None,
        "dayLabel": entry.get("dayLabel") or "",
        "timeStart": entry.get("timeStart") or None,
        "timeEnd": entry.get("timeEnd") or None,
        "timeRaw": entry.get("timeRaw") or "",
        "displayName": category if category != "Mass" else "Holy Mass",
        "language": entry.get("language") or None,
        "notes": entry.get("notes") or None,
        "pattern": None,
        "eventDate": None,
    }
