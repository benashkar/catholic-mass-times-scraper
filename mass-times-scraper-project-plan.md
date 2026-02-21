# Catholic Mass Times Scraper — Project Plan

## Project Overview

**Project:** Automated Catholic Mass Times Scraper for CR Community News
**Owner:** Ben Ashkar (Healthy Analytics)
**Scope:** Scrape, parse, and publish weekly Catholic mass times — starting with 16 Ohio communities, scaling to all US cities
**Final Output:** Date-specific mass time listings for newspaper publication (story + table format)
**Long-Term Vision:** A comprehensive, continuously updated database of Catholic mass times for every parish in the United States, powering CR Community News editions in any market
**GitHub:** https://github.com/benashkar/catholic-mass-times-scraper

---

## Target Communities

| # | Community | County/Area | CatholicIndex Page? | Fallback City |
|---|-----------|-------------|---------------------|---------------|
| 1 | Canal Winchester | Franklin | Yes | — |
| 2 | Groveport | Franklin | Yes | — |
| 3 | Hilliard | Franklin | Yes | — |
| 4 | Grove City | Franklin | Yes | — |
| 5 | Obetz | Franklin | No | Columbus |
| 6 | Hamilton Township | Franklin | No | Columbus |
| 7 | Lithopolis | Fairfield | No | Columbus |
| 8 | Lockbourne | Franklin | No | Columbus |
| 9 | Madison Township | Franklin | No | Columbus |
| 10 | West Columbus | Franklin | No | Columbus |
| 11 | Lincoln Village | Franklin | No | Columbus |
| 12 | Prairie Township | Franklin | No | Columbus |
| 13 | Westgate | Franklin | No | Columbus |
| 14 | Galloway | Franklin | No | Columbus |
| 15 | Urbancrest | Franklin | No | Columbus |
| 16 | Commercial Point | Pickaway | No | Columbus |

> **Note:** 4 communities have their own CatholicIndex city page. The other 12 use the Columbus page as fallback, with response caching so Columbus is only fetched once.

---

## Data Sources (Priority Order)

### Tier 1: CatholicIndex.org (Primary) — ACTIVE
- **Architecture:** Next.js App Router application (NOT server-side rendered HTML as originally assumed)
- **Data delivery:** All data embedded as JSON inside React Server Component (RSC) flight payloads in `self.__next_f.push()` script tags — no HTML parsing needed
- **Coverage:** 20,000+ parishes, updated weekly with automatic change detection
- **Data available:** Church name, address, phone, website, livestream URL, day-by-day mass times, confession times, adoration hours, devotions, special events, monthly services, community insights (user reviews)
- **Data NOT available:** Pastor/clergy names, photos, staff directories
- **URL patterns:**
  - City pages: `catholicindex.org/mass-times/{city}-{state}` → contains `initialChurches` array
  - Church detail: `catholicindex.org/churches/{slug}` → contains `data.church` + `data.services` + `communityInsights`
  - Search: `catholicindex.org/search`
- **Key data structures:**
  - City page: `initialChurches[]` with slug, name, street, city, stateRegion, lat/lng, phone, website, massCount, confessionCount, adorationCount, upcomingMasses, hasPerpetualAdoration
  - Church detail: `data.services` object organized by category (Mass[], Confession[], Adoration[], Devotions[], Education[], Community[], Other[])
  - Each service record: serviceId, category, scheduleType, dayOfWeek, timeStart, timeEnd, displayName, language, location, eventDate, pattern, timeRelation, referenceService, offsetMinutes, notes

### Tier 2: DiscoverMass.com (Backup / Cross-Reference)
- **Status:** Not yet integrated. CatholicIndex coverage has been sufficient for Phase 1.
- **Data available:** Mass times, confession, adoration, directions
- **Data NOT available:** Pastor/clergy names

### Tier 3: MassTimes.org (Largest DB, Harder to Scrape)
- **Coverage:** 117,000+ churches worldwide — most comprehensive
- **Problem:** Entirely JavaScript-rendered (AngularJS SPA). Requires headless browser.
- **Use case:** Only if Tiers 1 and 2 have gaps

### Pastor/Clergy Data Sources (NOT YET INTEGRATED)
- **CatholicIndex:** Does NOT have structured clergy data. Only mention of priests is in free-text `communityInsights` (user reviews) — unreliable.
- **DiscoverMass:** Does NOT have clergy data.
- **Diocese of Columbus (columbuscatholic.org):** Parish directory has name/address/phone only. Priest assignment announcements have structured data but are published as news articles, not a queryable API.
- **CatholicParishDirectory.com:** COMMERCIAL product — has pastor names + emails in Excel, updated weekly. Best structured source.
- **CatholicData.org:** API-based provider covering 17,600+ parishes. Another commercial option.
- **Individual parish websites:** Each has its own format — significant scraping effort per site.
- **Decision needed:** Choose a clergy data source for the `clergy` and `clergy_assignment` tables.

### Not Recommended
- **MassTime.us** — JS-rendered, smaller database
- **Diocese of Columbus (columbuscatholic.org)** — No centralized mass times; links to individual parish sites only
- **CatholicDirectory.com** — Less structured data, connection issues encountered

---

## Data Schema — PostgreSQL (Maximally Normalized)

> **Design philosophy:** Maximum normalization. Every field that can be a lookup table, enum, or boolean IS one. No free-text strings where structured data exists. Schema lives at `database/schema.sql`.

### Lookup Tables (11 tables — no raw strings!)

| Table | Purpose | PK Type | Example Values |
|-------|---------|---------|----------------|
| `lk_state` | US states | CHAR(2) | OH, PA, IN |
| `lk_diocese` | Catholic dioceses | SERIAL | Diocese of Columbus |
| `lk_service_category` | Service types (7) | VARCHAR(20) | mass, confession, adoration, devotions, education, community, other |
| `lk_schedule_type` | Recurrence types (8) | VARCHAR(20) | sunday, saturday, weekday, specific_weekday, monthly, special_event, parish_event, other |
| `lk_language` | Service languages (8) | VARCHAR(20) | en, es, la, bi, vi, ko, pl, pt |
| `lk_day_of_week` | Days with sort + is_weekend | CHAR(3) | Mon (1), Tue (2), ..., Sun (7) |
| `lk_recurrence_pattern` | Monthly patterns (6) | VARCHAR(50) | first_friday, first_saturday, first_sunday, thursday_before_first_friday |
| `lk_time_relation` | Relative timing (2) | VARCHAR(10) | before, after |
| `lk_clergy_role` | Priest/deacon roles (7) | VARCHAR(30) | pastor, parochial_vicar, deacon, administrator |
| `lk_note_tag` | Structured note tags (16) | VARCHAR(30) | vigil, by_appointment, 24_hours, school_mass, holy_day, bilingual, exposition, etc. |

### Core Entity Tables (8 tables)

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `community` | 16 target communities | name, county, state_code, zip_codes, fallback_city, lat/lng, is_active |
| `church` | 79 churches | slug (natural key), name, address, lat/lng, phone, website, diocese_id, has_perpetual_adoration, has_livestream, schedule_updated_at, community_insights |
| `church_community` | Many-to-many junction | church_id, community_id, distance_miles |
| `clergy` | Priests/deacons (NOT YET POPULATED) | prefix, first_name, last_name, suffix, is_active |
| `clergy_assignment` | Clergy-to-church assignments | clergy_id, church_id, role_code, effective_date, end_date, is_current |
| `service` | All services (masses, confessions, etc.) | church_id, source_service_id, category_code, schedule_type_code, day_code, time_start/end, event_date, language_code, pattern_code, relation_code, display_name, notes_raw |
| `service_note_tag` | Parsed tags from notes (M2M) | service_id, tag_code |
| `scrape_log` | Audit trail of scrape runs | scrape_type, started_at, status, churches_scraped, errors |

### Convenience Views (4 views)

| View | Purpose |
|------|---------|
| `v_sunday_masses` | All active Sunday masses with church details — the newspaper listing query |
| `v_weekly_schedule` | Full weekly schedule organized by church → day → time |
| `v_confession_times` | All active confession times with relative-time support |
| `v_church_summary` | Dashboard: all churches with computed service counts |

### ETL Transformer Module (`src/etl/transformers.py`)

Maps CatholicIndex raw values to normalized DB codes:
- `CATEGORY_MAP`: "Mass" → "mass", "Confession" → "confession", etc.
- `LANGUAGE_MAP`: "spanish" → "es", "latin" → "la", "bilingual" → "bi", etc.
- `PATTERN_MAP`: "first_friday_(recurring)" → "first_friday", etc.
- `STATE_MAP`: "Ohio" → "OH" (all 50 states + DC)
- `parse_note_tags()`: Regex-based extraction of 16 structured tags from free-text notes
- `transform_church()`, `transform_service()`, `transform_community()`: Full dict-to-DB-row transformers

---

## Phase Plan

### Phase 1: Church Discovery & Mapping ✅ COMPLETE
**Goal:** Build a complete list of Catholic churches serving each of the 16 communities.

- [x] For each community, determine the relevant ZIP codes and a search radius
- [x] Scrape CatholicIndex.org city/search pages to get all church URLs per community
- [ ] Cross-reference with DiscoverMass.com to catch any gaps
- [ ] Cross-reference with Diocese of Columbus parish list for completeness
- [x] Build a master church list CSV with all address/contact fields
- [ ] Manual QA: verify no major parishes are missing

**Results (2026-02-20):**
- **16/16 communities scraped successfully**
- **79 unique churches found** within 25-mile radius
- 4 communities had their own CatholicIndex city page (Canal Winchester, Groveport, Hilliard, Grove City)
- 12 communities used Columbus as a fallback (smaller towns/townships/CDPs without their own page)
- Master church list saved to `data/churches/master_church_list.csv`
- Per-community JSON files saved to `data/churches/{community_name}.json`

**Key technical finding:** CatholicIndex is a **Next.js App Router** app. All data is embedded as React Server Component (RSC) flight payloads in the HTML — no HTML parsing needed. We extract JSON directly from `self.__next_f.push()` calls. This is much more reliable than parsing rendered HTML.

### Phase 2: Mass Time Scraper (Recurring Schedules) — IN PROGRESS
**Goal:** Scrape and parse weekly recurring mass times for every church in the master list.

- [x] Build RSC data extractor for CatholicIndex pages (`src/parsers/rsc_extractor.py`)
- [x] Write unit tests alongside each parser function (22 tests passing)
- [x] Add logging to all scraper functions (console + file logging)
- [x] Parse mass times by day of week (extracted from `data.services.Mass` via RSC)
- [x] Parse confession times with start/end time ranges (data arrives pre-structured)
- [x] Parse adoration hours (extracted from `data.services.Adoration`)
- [x] Parse monthly services (First Friday, etc. — `pattern` field in service records)
- [x] Parse date-specific special events (Holy Days — `eventDate` field)
- [x] Store all data in structured JSON/CSV
- [x] Add `last_updated` tracking per church (CatholicIndex `updatedAt` field)
- [x] Sample scrape: 10/10 churches near Grove City — all successful with full schedule data
- [x] **Design PostgreSQL schema** — maximally normalized with 11 lookup tables (done early, ahead of Phase 5)
- [x] **Build ETL transformers** — `src/etl/transformers.py` with complete value mappings + 41 tests
- [ ] Build DiscoverMass.com parser as fallback
- [ ] Build `run_scrape_details.py` to fetch full schedules for all 79 churches
- [ ] Investigate pastor/clergy data sources (see "Pastor/Clergy Data Sources" section above)

### Phase 3: Date Generation Layer
**Goal:** Convert recurring day-of-week schedules into specific dated listings for newspaper publication.

- [ ] Input: publication date range (e.g., "March 7–13, 2026")
- [ ] Map each recurring schedule entry to actual calendar dates in the window
- [ ] Include Saturday vigil masses (map to the following Sunday's listing or keep separate — editorial decision)
- [ ] Flag upcoming Holy Days of Obligation within the date range
- [ ] Generate two output formats:
  - **Table format:** Structured CSV/spreadsheet for newspaper layout
  - **Story format:** Narrative text block for article-style listing

**Example table output:**
```
Church                         | Date              | Day       | Time
Our Lady of Perpetual Help     | Sat, Mar 7, 2026  | Saturday  | 4:00 PM (Vigil)
Our Lady of Perpetual Help     | Sun, Mar 8, 2026  | Sunday    | 8:30 AM
Our Lady of Perpetual Help     | Sun, Mar 8, 2026  | Sunday    | 10:30 AM
St. John XXIII                 | Sat, Mar 7, 2026  | Saturday  | 5:00 PM (Vigil)
...
```

### Phase 4: Scheduling & Automation
**Goal:** Run the scraper on a recurring schedule and detect changes.

- [ ] Dockerize the project
- [ ] Weekly automated scrape: run Sunday night / Monday morning
- [ ] Change detection: compare current scrape to previous; flag schedule changes
- [ ] Special events mode: pull date-specific events from CatholicIndex
- [ ] Holy Day alerts: flag upcoming Holy Days so editorial can follow up with parishes
- [ ] Slack/email alerts for scrape failures and anomalies
- [ ] Store scrape history via `scrape_log` table

### Phase 5: Ohio Statewide Expansion
**Goal:** Expand coverage to all Catholic parishes across Ohio.

- [ ] Compile master list of all Ohio cities/towns/CDPs
- [ ] Map all Ohio ZIP codes to their serving parishes
- [ ] Run scraper against all Ohio communities using CatholicIndex state-level pages
- [ ] Cross-reference with all 6 Ohio dioceses: Columbus, Cincinnati, Cleveland, Toledo, Youngstown, Steubenville
- [ ] Validate coverage against the Official Catholic Directory (OCD)
- [ ] Migrate from flat files to PostgreSQL (schema already designed)
- [ ] Optimize scraper performance for larger runs

### Phase 6: National Expansion — Region by Region
**Goal:** Scale to all ~17,000 Catholic parishes in the United States.

**Sub-phase 6a: Data Architecture for National Scale**
- [x] Design PostgreSQL schema (done in Phase 2, ready for national scale)
- [ ] Add geographic hierarchy: state → diocese → deanery → parish
- [ ] Integrate USPS city/ZIP reference data
- [ ] Build incremental scrape logic (based on CatholicIndex `updatedAt` field)
- [ ] Add pastor/clergy data integration (from chosen data source)

**Sub-phase 6b: Diocese-by-Diocese Rollout**
- [ ] Compile master list of all 196 US dioceses/archdioceses
- [ ] Prioritize by CR Community News expansion markets
- [ ] For each diocese: scrape CatholicIndex, cross-reference DiscoverMass + diocesan directory
- [ ] Track coverage: % of known parishes scraped per diocese/state

**Sub-phase 6c: Data Quality & Enrichment**
- [ ] Parishes with no mass times listed
- [ ] Stale schedules (no update in 6+ months)
- [ ] Duplicate parish detection
- [ ] Google Places API for address verification
- [ ] Confidence score per parish

**Sub-phase 6d: Automation at Scale**
- [ ] Full refresh monthly, incremental weekly
- [ ] Change detection alerts
- [ ] Coverage dashboard by state/diocese
- [ ] API for CR Community News market-specific pulls

### Phase 7: Ongoing Maintenance & Future Enhancements
- [ ] Monitor source sites for structural changes
- [ ] Add new data sources as they emerge
- [ ] Potential: other denominations, mobile-friendly web lookup, parish bulletin scraping

---

## National Scale Reference Numbers

| Metric | Approximate Count |
|--------|-------------------|
| US Catholic parishes | ~17,000 |
| US dioceses/archdioceses | 196 |
| US states + DC + territories | 56 |
| Ohio Catholic parishes | ~800 |
| Ohio dioceses | 6 (Columbus, Cincinnati, Cleveland, Toledo, Youngstown, Steubenville) |
| CatholicIndex.org coverage | 20,000+ parishes |
| MassTimes.org coverage | 117,000+ churches (worldwide) |

---

## Technical Stack

| Component | Tool | Notes |
|-----------|------|-------|
| Language | Python 3.14 | Running on Windows |
| Scraping | `requests` library | Fetches raw HTML containing RSC payloads |
| Data extraction | Custom RSC extractor (`src/parsers/rsc_extractor.py`) | Parses Next.js React Server Component flight data via `json.loads()` |
| ETL transformers | `src/etl/transformers.py` | Maps CatholicIndex values → normalized DB codes (41 tests) |
| JS-rendered pages (if needed) | Playwright | For MassTimes.org or stubborn sites (not needed for CatholicIndex) |
| Data storage (Phase 1–4) | JSON + CSV flat files | 16 communities, 79 parishes discovered, 10 detailed schedules |
| Data storage (Phase 5+) | PostgreSQL | Schema ready at `database/schema.sql` — 11 lookup + 8 entity tables |
| Rate limiting | Custom (`src/utils/http.py`, 1.5s between requests) | Exponential backoff on retries, no retry on 404s |
| Logging | Python `logging` module (`src/utils/logger.py`) | Console (INFO) + file (DEBUG) dual output to `logs/scrape_YYYY-MM-DD.log` |
| Testing | `pytest` (66 tests passing) | RSC extractor (22) + smoke (3) + ETL transformers (41) |
| Output generation | Python (CSV, Markdown, or direct layout format) | Per-market newspaper listings |
| Scheduling (future) | Cron job or GitHub Actions | Weekly incremental, monthly full |
| Version control | Git + GitHub | https://github.com/benashkar/catholic-mass-times-scraper |

---

## File Structure

```
church scrapes/
├── mass-times-scraper-project-plan.md    # This file — master project plan
├── requirements.txt                       # Python dependencies
├── pyproject.toml                         # pytest config
├── .env.example                           # Environment variable template
├── .gitignore
│
├── config/
│   ├── __init__.py
│   └── settings.py                        # Central config: paths, URLs, 16 target communities
│
├── database/
│   └── schema.sql                         # PostgreSQL schema (11 lookup + 8 entity tables + 4 views)
│
├── src/
│   ├── __init__.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   └── catholic_index.py              # CatholicIndex scraper (discover + detail)
│   ├── parsers/
│   │   ├── __init__.py
│   │   └── rsc_extractor.py               # RSC flight payload JSON extractor
│   ├── etl/
│   │   ├── __init__.py
│   │   └── transformers.py                # Value mappings: CatholicIndex → DB codes
│   └── utils/
│       ├── __init__.py
│       ├── logger.py                      # Dual-output logging (console + file)
│       ├── http.py                        # HTTP wrapper with rate limiting + retries
│       └── file_io.py                     # CSV/JSON read/write helpers
│
├── tests/
│   ├── __init__.py
│   ├── fixtures/                          # Sample HTML/JSON for unit tests
│   ├── test_smoke.py                      # 3 config/import smoke tests
│   ├── test_rsc_extractor.py              # 22 RSC extractor tests
│   └── test_transformers.py               # 41 ETL transformer tests
│
├── data/
│   ├── churches/
│   │   ├── master_church_list.csv         # 79 unique churches (Phase 1 output)
│   │   ├── canal_winchester.json          # Per-community church lists (16 files)
│   │   ├── groveport.json
│   │   └── ... (14 more)
│   └── output/
│       └── grove_city_sample.json         # Full schedule data for 10 churches (sample)
│
├── logs/                                  # Auto-generated log files (scrape_YYYY-MM-DD.log)
│
├── run_discovery.py                       # Phase 1: discover churches for all 16 communities
└── run_scrape_sample.py                   # Phase 2: sample scrape of 10 churches near a community
```

---

## Coding Standards — Junior Developer Friendly

> **Important:** This project will be managed by a junior developer who is new to Python. All code must be written with extensive documentation and clear structure to support learning and independent troubleshooting.

### Comment Requirements (Apply to ALL Code)

- **Every file** must start with a module-level docstring explaining what the file does, what inputs it expects, and what outputs it produces
- **Every function** must have a docstring explaining: what it does, what each parameter means, what it returns, and an example usage where helpful
- **Every non-obvious line or block** must have an inline comment explaining *why* it does what it does
- **Complex logic** (regex, parsing, data transformations) must have step-by-step comments
- Use **descriptive variable names** — `church_address_parts` not `cap`, `mass_time_str` not `mts`
- Include **"WHY" comments** for design decisions
- Add **WARNING comments** for common pitfalls

### File & Folder Naming
- Use **snake_case** for all Python files
- Group related files into folders
- Keep a top-level `CONTRIBUTING.md` with setup instructions and development workflow

---

## Unique Data Values Found (from 10-church sample scrape)

These inform the lookup table contents in the database schema:

| Field | Count | Values |
|-------|-------|--------|
| **categories** | 7 | Mass, Confession, Adoration, Devotions, Other, Education, Community |
| **scheduleTypes** | 8 | sunday, saturday, weekday, specific_weekday, monthly, special_event, parish_event, other |
| **languages** | 4+null | english, spanish, bilingual, latin (null = English default) |
| **daysOfWeek** | 7+null | Mon, Tue, Wed, Thu, Fri, Sat, Sun (null for special events) |
| **patterns** | 5 | first_friday_(recurring), first_saturday_(recurring), first_sunday, thursday_(before_first_friday), null |
| **timeRelations** | 2+null | before, after (null for absolute-time services) |
| **referenceServices** | 1+null | Mass (null for absolute-time services) |
| **locations** | 8 | Church, Church Hall, Holy Family Church, Saint Aloysius Church, St. Mary, St. Mary Magdalene, school gym, null |
| **displayNames** | 32 | Free-text — too variable for enum (Holy Mass, Vigil, Rosary & Divine Mercy Chaplet, etc.) |
| **notes** | 37 unique | Free-text — parsed into structured tags via `parse_note_tags()` |
| **offsetMinutes** | 1+null | 30 (only non-null value seen so far) |

---

## Known Issues & Fixes (Historical)

1. **Python 3.14 pandas build failure:** `pandas==2.2.*` couldn't build on Python 3.14 (meson/Visual Studio issue). **Fixed:** Relaxed version constraints to `pandas>=2.2` and `lxml>=5.3`.
2. **12/16 communities 404'd on CatholicIndex:** Small towns don't have city pages. **Fixed:** Added `fallback_city: "Columbus"` to config with response caching in `run_discovery.py`.
3. **404 retries wasted time:** Each 404 was retried 3x with exponential backoff. **Fixed:** Early return for 404 status codes in `http.py` (404 is permanent, not transient).
4. **"Invalid \escape" JSON parsing on church details:** Manual string replacement missed escape sequences in community insights text. **Fixed:** Using `json.loads(f'"{match}"')` for proper JS string unescaping with manual fallback.
5. **TypeError on None values in formatting:** `.get('dayOfWeek', '?')` returns None (not '?') when key exists with None value. **Fixed:** Changed to `.get('dayOfWeek') or '?'` pattern.
6. **Confession time range parsing (non-bug):** Originally assumed "3:00pm-3:45pm" needed parsing. CatholicIndex RSC data provides confession times pre-structured with separate `timeStart` and `timeEnd` fields.

---

## Git Commit History

| Commit | Message | Date |
|--------|---------|------|
| `554a30b` | Initial project scaffolding for Catholic Mass Times Scraper | 2026-02-20 |
| `6b127df` | Add Phase 1 church discovery: 79 churches found across 16 Ohio communities | 2026-02-20 |
| `4a09b29` | Fix RSC extractor JSON decoding and add sample scrape script | 2026-02-20 |
| `8e0f117` | Add maximally normalized PostgreSQL schema and ETL transformers | 2026-02-20 |

---

## Editorial Considerations

- **Holy Week / Christmas / Holy Days:** Special schedules published by parishes 2–4 weeks before events. Scraper flags these dates so editorial can follow up.
- **Language masses:** Some parishes offer masses in Spanish, Vietnamese, etc. Language field included in schema and output.
- **Pastor/clergy attribution:** Not yet available from CatholicIndex. Schema includes `clergy` + `clergy_assignment` tables for when data source is chosen.
- **Disclaimer:** "Mass times are subject to change. Please verify with your parish before attending."
- **Frequency decision needed:** Does the newspaper publish mass times every issue, or just monthly?
- **Grouping decision needed:** Group by church or by date?

---

## Next Steps (Immediate)

1. **Build `run_scrape_details.py`** — Fetch full weekly schedules for all 79 churches from their individual CatholicIndex pages
2. **Build the date generation layer** (Phase 3) — Convert recurring schedules to specific dated newspaper listings
3. **Generate a sample newspaper-ready output** for one publication week across all 16 communities
4. **Review with editorial team** for format/layout preferences
5. **Choose a pastor/clergy data source** — Decision needed on whether to purchase CatholicParishDirectory.com or scrape individual parish websites
6. **Set up PostgreSQL** and run `database/schema.sql` to create the production database
7. **Build database loader** — Script to INSERT transformed data into PostgreSQL using the ETL transformers
