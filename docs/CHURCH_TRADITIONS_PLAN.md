# Church Times, Events & PDFs — All Christian Traditions, CR Markets

**Repo:** `benashkar/catholic-mass-times-scraper` · **Branch:** `feature/all-traditions`
**Scope:** every active Cherry Road market (46 papers, 63 counties, 15 states — `data/reference/cr_markets.csv`), Brownwood Bulletin (Brown County, TX) first.
**Goal, in order:** service times → events → PDFs (bulletins/newsletters → existing name pipeline), for Catholic (done), Protestant, and any other Christian tradition with a cheap centralized source.

## Context

CatholicIndex.org gives the Catholic side churches + mass times + bulletin links in one payload. Nothing like it exists for Protestants (verified 2026-09-17 against every open nationwide/state directory — see "Sources"). Two traditions *do* have a CatholicIndex-grade source: **Jehovah's Witnesses** and **LDS** both expose open JSON locators with structured meeting times. Everything else follows a two-layer pattern: nationwide directories give the **roster** (name, address, denomination, website), and the church's **own website** gives times, events and PDFs — which is where events and PDFs live for every tradition anyway, Catholic included.

This is built as a feature branch of the Catholic repo rather than a new repo because the pipeline shape, db99 tables, Render cron jobs, dashboard and bulletin/name engine are all shared. Only the discovery and times-extraction layers are tradition-specific.

## Sources (verified live 2026-09-17)

**Tier A — structured times from one JSON call (build first, trivial):**

| Tradition | Endpoint | Notes |
|---|---|---|
| Jehovah's Witnesses | `GET https://hub.jw.org/meetings/api/meeting-search?meetingType=meetings&northEastLatitude=..&northEastLongitude=..&southWestLatitude=..&southWestLongitude=..` | bbox required; returns `midweekMeetingDay/Time`, `weekendMeetingDay/Time`, language, address. No auth. |
| LDS | `GET https://maps.churchofjesuschrist.org/api/maps-proxy/v2/locations/identify?coordinates=<lng>,<lat>&layers=WARD&radius=<m>` then `/locations/{unitNumber}` | Must send header `Referer: https://maps.churchofjesuschrist.org/`. `hours.code` e.g. `"Su 10:00-12:00"`, `hours.primary.display`. |
| Catholic | CatholicIndex (existing `src/scrapers/catholic_index.py`) | unchanged |

**Tier B — roster with times sometimes filled (cheap HTML/JSON):**

| Source | Coverage | Endpoint | Times |
|---|---|---|---|
| churchfinder.com | all denominations, nationwide | `/churches/<st>/<city-slug>?page=N` → profile `/churches/<st>/<city>/<slug>` | "Service Times" field, ~10% filled |
| churchzip.com | Churches of Christ, nationwide | `select.php?querytype=location&center_latitude=..&center_longitude=..` | free-text `qa2`, ~15% filled |
| UMC | nationwide | `POST umc.org/ChurchesFeature/Churches/GetChurches` `{latitude,longitude,countryCode:"<City>, <ST>, USA",Page,distance:"25"}` → `/en/find-a-church/church?id=` | `worship_1..5` / "WORSHIP TIMES" block, sparse |
| UMC annual-conference sites | per conference (e.g. `ctcumc.org/churches`) | HTML | structured where present — add per market as found |
| OCA (Orthodox) | ~700 parishes | `oca.org/parishes/state/<ST>` → parish page | "Schedule of Services" free text |
| Episcopal Asset Map | nationwide | `/search/places/batch?offset=0&limit=10000&display=block_4` (JSON index) → `/node/{nid}` HTML | "Worship times" section, partial |

**Tier C — roster only (no times; feeds the website crawl):** Texas Baptists (`texasbaptists.org/actions/salesforce-data/churches/export?church&location=<City>%2C%20TX&pp=50`), SBC (`churches.sbc.net/wp-json/wp/v2/church?search=`), Global Methodist (`api.storepoint.co/v1/1682b4fc2190ec/locations?rq`), Nazarene ArcGIS (`maps.nazarene.org/ArcGIS/rest/services/Nazarene/NazareneChurches/MapServer/0/query?where=State%3D%27TX%27&outFields=*&f=json`), ACNA (all 1,007 congregations inline on `acna.org/anglican_church/map`), Assembly of Bishops Orthodox proximity search (HTML).

**Skipped in v1 (Cloudflare / signed API):** Assemblies of God, SBTC, church-of-christ.org, LCMS, Adventist, ELCA, faithstreet. Their churches still arrive via churchfinder + website crawl. Google Places not used (paid; "hours" ≠ services).

## Approach

### 0. Market scoping (new, shared by every stage)
- `data/reference/cr_markets.csv` — market, state, active, county, fips, source. Built from `cherry_road_dashboard/frontend/public/scopes/cr.json` + `counties/US.json` + `backend/active_markets.py`. 7 active papers had no footprint in the dashboard and are inferred from a sibling paper in the same county (`source=inferred-sibling`); confirm those.
- `src/markets.py` — `load_markets(active_only=True)`, `counties_for_market(name)`, `cities_for_county(state, county)` (join to `data/city_lists/us_cities.csv`, which has county), `bbox_for_county(...)` (from the county's cities' lat/lng, padded 15 mi). Every runner takes `--market "TX - Brownwood Bulletin"` or `--all-active` instead of `--state`.
- `church` gets `market` (nullable; a church can sit in a county no market covers) — set by county match at load time using the county-name normalization already needed (`" County"` stripping, `St.`/`St`).

### 1. Roster — `src/sources/<name>.py`, one adapter per source, common `RawChurch` dataclass
`fetch(scope) -> list[RawChurch]` where scope is a county bbox / city list. Adapters: `jw.py`, `lds.py` (Tier A, times inline), `churchfinder.py`, `churchzip.py`, `umc.py`, `oca.py`, `episcopal.py` (Tier B), `texas_baptists.py`, `sbc.py`, `gmc.py`, `nazarene.py`, `acna.py` (Tier C).
`src/etl/dedup.py` merges across sources on normalized name + `address_parser.py` output / geo (<150 m); keeps `tradition`, `denomination`, `denomination_family`, `source_ids` JSON, best `website_url`.

### 2. Times from church websites — `src/website/`
- `crawler.py`: homepage + up to 6 candidate pages (`/visit`, `/service-times`, `/worship`, `/im-new`, `/about`, `/contact`, nav links matching those words); reuse `fetch_page` (rate limit + retries); Playwright fallback only when the page has no text.
- `structured.py`: schema.org JSON-LD (`Church`, `openingHoursSpecification`, `Event`), CMS fingerprints (Subsplash, Clover, Tithe.ly, Church Center, Wix, Squarespace), then regex on `(Sun|Sunday|Wed|Wednesday)…\d{1,2}(:\d{2})?\s*(a|p)\.?m` blocks.
- `llm_extract.py`: Claude Haiku over trimmed page text → `[{day,time_start,time_end?,service_type,label,language?,location_note?}]` + confidence; gated by `LLM_EXTRACT=1`. Runs only where structured/regex found nothing.
- Applies to Catholic parishes too (parish sites hold events/PDFs CatholicIndex doesn't).

### 3. Events — `src/website/events.py`
Discover `/events`, `/calendar`, `.ics` links, Google Calendar embeds (→ public iCal), Church Center calendar JSON, schema.org `Event`. Parse with `icalendar`; page-text events go through `llm_extract` with an Event schema. JW `meetingType=conventions|assemblies|memorials` and LDS stake events are extra event sources from Tier A. New table `church_event` (church_id, title, start, end, location, description, source_url, content_hash).

### 4. PDFs — `src/website/pdfs.py`
`<a href="*.pdf">` plus link text matching bulletin|newsletter|announcements|order of worship; log Issuu/Flipsnack embeds. Insert into existing `bulletin_source`/`bulletin_pdf` with `discovery_source='website'`; existing `extract_bulletins_to_db99.py` picks them up. Extend honorific patterns in `run_bulletin_scraper.py`/`extract_bulletins_to_db99.py` with Pastor, Rev., Bro., Sis., Minister, Elder, Bishop, Presiding Overseer (JW), Ward/Stake President (LDS).

### Schema (MySQL db99 `church_scrapes`, via idempotent `ensure_schema()` in `sync_to_db99.py` style)
- `church`: + `tradition` (catholic|protestant|lds|jw|orthodox|anglican|other), `denomination`, `denomination_family`, `website_url`, `source_ids` JSON, `market`, `times_source`, `times_confidence`. Existing Catholic rows backfilled `tradition='catholic'`.
- `service`: + `raw_text`, `extract_method` (directory|jsonld|regex|llm|api); `category_code` gains worship, sunday_school, bible_study, youth, prayer, midweek, small_group, sacrament_meeting, midweek_meeting, weekend_meeting, divine_liturgy, vespers.
- New: `church_event`, `church_crawl` (church_id, url, fetched_at, status, pages_found, pdfs_found) for resumability.
- Dashboard: `tradition` + `market` filters on the existing state/city/church pages (`dashboard/app/routes/mass_times.py`, `data_loader.py`).

### Runners
- `run_roster.py --market "TX - Brownwood Bulletin" | --all-active [--sources jw,lds,churchfinder,...]`
- `run_websites.py --market ... [--only-missing-times] [--tradition protestant]` (stages 2–4, resumable via `church_crawl`)
- `run_daily_pipeline.py` gains steps: Tier A refresh (weekly), website crawl slice (daily, `--only-missing-times` + stale >30 d), then existing bulletin/rescore/health/redeploy. `render.yaml`: no new services; the two existing cron jobs pick up the new steps.

## Build order
1. Branch `feature/all-traditions`; add `cr_markets.csv`, `src/markets.py`, `ensure_schema()` additions, `tradition='catholic'` backfill.
2. Tier A adapters (`jw.py`, `lds.py`) → run for Brown County → rows in `church`/`service` with `extract_method='api'`. This is the first visible win.
3. Tier B/C adapters + dedup → Brown County roster (~80–100 Protestant + Tier A). Eyeball.
4. Website crawl structured+regex on the roster; measure; enable LLM pass; measure. Target ≥70% of roster with a Sunday time.
5. Events + PDFs on the same set; confirm PDFs flow into the name pipeline.
6. `--all-active` roster + Tier A across all 46 markets; website crawl as daily slices.
7. Dashboard filters; merge to master once Brownwood + one non-TX market look right.

## Verification
- Roster spot-check (Brownwood): First Baptist Brownwood, Coggin Ave Baptist, First Methodist Brownwood, Woodland Heights CoC, Grace Lutheran, St. John's Episcopal, Victory Life, Early CoC, Brownwood Ward (LDS, 4503 4th St, Su 10:00), Kingdom Hall Early (510 Lucas Dr, Thu 19:30 / Sun 10:00) — each exactly once with correct tradition/denomination/website/market.
- Times: hand-compare extracted services with the live sites for those ten; fixture tests per CMS fingerprint in `tests/fixtures/websites/`; unit tests for the regex, churchzip `qa2`, LDS `hours.code`, JW day/time parsing.
- Market join: `SELECT market, COUNT(*)` matches expectations; no church in an active county with `market IS NULL`.
- Events/PDFs: ≥1 event and ≥1 PDF for churches known to publish them.
- Health: extend `tests/test_pipeline_health.py` — % churches with times by tradition, crawl error rate, market coverage.
- Render: one manual job run per stage before letting cron pick it up.

## Kickoff prompt for Claude Code

Open Claude Code in `catholic_mass_times_scraper` and paste:

> Read `docs/CHURCH_TRADITIONS_PLAN.md`. Create branch `feature/all-traditions`. Do build-order steps 1 and 2 only: add `src/markets.py` over `data/reference/cr_markets.csv`, the `ensure_schema()` additions with the `tradition='catholic'` backfill, and the JW and LDS adapters in `src/sources/` with a `run_roster.py --market "TX - Brownwood Bulletin" --sources jw,lds`. Run it against db99 with `DB_HOST=10.10.0.8` and show me the resulting `church` and `service` rows for Brown County before starting step 3.
