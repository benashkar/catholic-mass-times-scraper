# Catholic Mass Times Scraper — Project Plan

## Project Overview

**Project:** Automated Catholic Mass Times Scraper for CR Community News
**Owner:** Ben Ashkar (Healthy Analytics)
**Scope:** Scrape, parse, and publish weekly Catholic mass times for every city in the United States
**Final Output:** Date-specific mass time listings for newspaper publication (story + table format)
**Long-Term Vision:** A comprehensive, continuously updated database of Catholic mass times for every parish in the United States (~17,000 parishes across 29,880 cities), powering CR Community News editions in any market
**Schedule:** Weekly automated scrape (manual trigger for now, automated via cron/GitHub Actions in Phase 4)
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

### Phase 2: Mass Time Scraper (Recurring Schedules) ✅ COMPLETE
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
- [x] **Full scrape of all 79 churches** — 79/79 success, 1,203 services, 1,774 dated instances
- [ ] Build DiscoverMass.com parser as fallback
- [ ] Investigate pastor/clergy data sources (see "Pastor/Clergy Data Sources" section above)

**Full Scrape Results (2026-02-20):**
- 79/79 churches scraped successfully (0 failures)
- 1,203 total service rows (masses, confessions, adoration, devotions, etc.)
- 1,774 dated service instances (next 2 weeks with day-of-week → actual dates)
- Output: `data/output/all_services.csv`, `data/output/dated_services.csv`, `data/output/all_churches_detail.json`

### Phase 3: Date Generation Layer ✅ COMPLETE
**Goal:** Convert recurring day-of-week schedules into specific dated listings for newspaper publication.

- [x] Map each recurring schedule entry to actual calendar dates (next 2 weeks)
- [x] Handle weekly recurrence (Mon–Sun day codes → actual dates)
- [x] Handle monthly patterns (First Friday, First Saturday, First Sunday, Thursday before First Friday)
- [x] Handle one-time events with specific dates (eventDate field)
- [x] Generate dated CSV with actual calendar dates
- [x] Extract CSV generation logic into shared module (`src/etl/csv_generator.py`)
- [ ] Input: custom publication date range (currently hardcoded to next 2 weeks)
- [ ] Story format output (narrative text block for article-style listing)
- [ ] Flag Holy Days of Obligation within date range

### Phase 4: Scheduling & Automation
**Goal:** Automatically re-scrape all states every Sunday so data stays fresh for weekly publication.

**Primary requirement: Fully automated Sunday refresh**
- [ ] GitHub Actions workflow to run `run_all_states.py` every Sunday (cron: `0 2 * * 0`)
- [ ] Or: Windows Task Scheduler / cron on local machine as fallback
- [ ] Auto-commit and push results after each state completes
- [ ] Slack/email notification on completion or failure

**Additional automation:**
- [ ] Dockerize the project for portable deployment
- [ ] Change detection: compare current scrape to previous; flag schedule changes
- [ ] Special events mode: pull date-specific events from CatholicIndex
- [ ] Holy Day alerts: flag upcoming Holy Days so editorial can follow up with parishes
- [ ] Store scrape history via `scrape_log` table

### Phase 5: Nationwide Statewide Expansion ✅ COMPLETE
**Goal:** Run the scraper against every city in every US state, building comprehensive parish coverage.

**Infrastructure (COMPLETE):**
- [x] Download complete US city list — 29,880 cities across 52 states/territories (`data/city_lists/us_cities.csv`)
- [x] Build `run_statewide.py` — CLI runner with resume capability, progress tracking, ETA display
- [x] Extract shared CSV generation into `src/etl/csv_generator.py`
- [x] Expand `_city_to_slug()` state map to all 50 states + DC
- [x] Add crash-safe JSONL incremental saves + progress JSON files
- [x] Verified CatholicIndex works for non-Ohio states (Texas/Houston confirmed: 200+ masses)

**Statewide Runner (`run_statewide.py`) Features:**
- CLI: `python run_statewide.py ohio texas --resume --limit 10`
- Discovery phase: queries every city in a state via CatholicIndex city pages
- Detail phase: scrapes full schedules for all discovered churches
- Resume: tracks completed cities/churches in progress JSON files
- JSONL: saves each church detail incrementally (crash-safe)
- 404 tolerance: most small towns return 404 (no CatholicIndex page) — that's expected
- ETA display: shows estimated time remaining during long runs
- Flags: `--resume`, `--limit N`, `--discovery-only`, `--detail-only`

**Output per state:**
- `data/churches/{state}/master_church_list.csv` — deduplicated church list
- `data/output/{state}/all_services.csv` — one row per service
- `data/output/{state}/dated_services.csv` — actual calendar dates for next 2 weeks
- `data/output/{state}/church_details.jsonl` — raw JSON per church (incremental)
- `data/output/{state}/parsed_addresses.csv` — segmented address fields

**Address Parser (`src/parsers/address_parser.py`):**
- Token-based street address parser (NOT single regex — handles edge cases better)
- Segments: street_number, pre_direction, street_name, street_suffix, post_direction, unit_type, unit_number
- Handles: route addresses (State Rt, County Rd, US Hwy, OH-46), "N St" ambiguity, Via prefix, St=Saint vs Street, post-directionals
- CLI: `python run_parse_addresses.py ohio texas` or `python run_parse_addresses.py all`
- Output: `data/output/{state}/parsed_addresses.csv`
- 43 tests in `tests/test_address_parser.py`

**Batch Runner (`run_all_states.py`):**
- Runs all remaining states sequentially: scrape → parse addresses → git commit
- Skips states that already have `church_details.jsonl`
- Resume-safe: just re-run if interrupted

**Rollout Progress (2026-02-22):**
- [x] All 50 states scraped, parsed, and committed to GitHub
- [x] Raw scrape totals: 31,972 records across 50 state directories

**City Counts by State (top 10):**
| State | Cities | State | Cities |
|-------|--------|-------|--------|
| PA | 2,579 | OH | 1,077 |
| TX | 1,775 | IL | 1,310 |
| NY | 1,556 | NJ | 862 |
| CA | 1,253 | MN | 872 |
| FL | 876 | WI | 734 |

### Phase 5.5: Data Quality — Dedup & Cross-State Cleanup ✅ COMPLETE
**Goal:** Remove duplicate records caused by CatholicIndex's 25-mile radius search and deduplicate hash-variant records.

**Problem identified (2026-02-22):**
CatholicIndex's API returns churches within a 25-mile radius of each queried city. For border cities, this pulls in churches from neighboring states. Combined with CatholicIndex occasionally listing the same church with slightly different names (different hash suffixes), this produced:
- **8,723 cross-state duplicates**: Same church (identical slug) appearing in multiple state directories
- **203 base-slug duplicates**: Same church with different 8-char hash suffixes (e.g., "Holy Family Parish (St. Mary Campus)" vs "Holy Family Parish St. Mary Campus")
- Worst-affected states: Delaware (81% out-of-state), Connecticut (55%), Indiana (53%), New Jersey (52%)

**Deduplication strategy:**
- Each CatholicIndex slug encodes the church's true state: `us-{state}-{city}-{name}-{hash}`
- Keep each church only in the state directory matching its slug state code
- For base-slug variants (same church, different hash), keep the most recently updated record
- 103 "orphan" churches (e.g., DC churches with no DC directory) kept in whichever state found them first

**Cleanup Script (`run_dedup_cleanup.py`):**
- CLI: `python run_dedup_cleanup.py` (dry run) or `python run_dedup_cleanup.py --apply`
- Loads all records → identifies orphans → classifies keep/remove → rewrites JSONLs
- Removes stale `resolve_progress.json` files after cleanup

**Results (2026-02-22):**

| Metric | Count |
|--------|-------|
| Records before cleanup | 31,972 |
| Records after cleanup | **23,046** |
| Cross-state removed | 8,723 |
| Base-slug dupes removed | 203 |
| States modified | 49 of 50 |
| Orphan records kept | 103 |

- All `parsed_addresses.csv` files regenerated post-cleanup

**Website URL Resolver (`run_resolve_urls.py`):**
- CatholicIndex website links are `/api/out?id=...&type=website&t=TIMESTAMP&sig=SIGNATURE` redirect URLs
- These serve an interstitial "Leaving Catholic Index" page with `window.location.href = "actual_url"` in JS
- Script fetches each interstitial page, parses actual URL via regex, adds `website_resolved` field to JSONL
- Rate limit: 0.3s between requests
- Resume support via `resolve_progress.json` per state
- CLI: `python run_resolve_urls.py ohio|all [--resume] [--limit N]`
- Tested on 10 Ohio churches: 10/10 resolved successfully
- Status: Pending full run on cleaned 23,046 records

### Phase 6: Data Quality & Enrichment
**Goal:** Ensure comprehensive, accurate coverage.

- [x] Duplicate parish detection — handled in Phase 5.5
- [ ] Resolve all website URLs to actual church websites (run_resolve_urls.py)
- [ ] Cross-reference with all 196 US dioceses/archdioceses
- [ ] Identify parishes with no mass times listed
- [ ] Flag stale schedules (no update in 6+ months)
- [ ] Google Places API for address verification
- [ ] Confidence score per parish
- [ ] Add pastor/clergy data integration (from chosen data source)
- [ ] Coverage dashboard by state/diocese

### Phase 7: Bulletin PDF Scraping & Name Extraction ⏳ IN PROGRESS
**Goal:** Download church bulletins (PDFs) from parish websites, extract text, and pull out all people's names mentioned.

**Use case:** Match names found in church bulletins against a known name list for community outreach / CR Community News.

**Pipeline (`run_bulletin_scraper.py`):**
```
Phase 1 — DISCOVER: Find bulletin pages on each church's website
Phase 2 — DOWNLOAD: Download the most recent bulletin PDFs (up to 3 per church)
Phase 3 — EXTRACT:  Extract text from PDFs (pdfplumber) + identify names via regex patterns
```

**CLI:**
```bash
python run_bulletin_scraper.py discover arizona             # Phase 1 only
python run_bulletin_scraper.py download arizona             # Phase 2 only
python run_bulletin_scraper.py extract arizona              # Phase 3 only
python run_bulletin_scraper.py all arizona                  # All 3 phases
python run_bulletin_scraper.py all arizona georgia          # Multiple states
python run_bulletin_scraper.py all arizona --limit 10       # Test on first 10 churches
python run_bulletin_scraper.py all arizona --resume         # Resume interrupted run
```

**Output per state:**
```
data/output/{state}/bulletin_discovery.json     — bulletin page URLs per church
data/output/{state}/bulletins/                   — downloaded PDF files
data/output/{state}/bulletin_texts/              — extracted text files
data/output/{state}/bulletin_names.csv           — extracted names (church, name, category, context)
data/output/{state}/bulletin_names.json          — same data in JSON format
data/output/{state}/bulletin_progress.json       — progress tracking for resume
```

**Bulletin Discovery Strategy (7 patterns identified):**

| # | Pattern | Platform | PDF URL Format | Example Site |
|---|---------|----------|---------------|--------------|
| 1 | WordPress + Simple File List plugin | WordPress | `wp-content/uploads/simple-file-list/bulletin-YYYY-MM-DD.pdf` | ickenmore.org |
| 2 | LPi / ParishesOnline.com (3rd party) | eCatholic/any | `container.parishesonline.com/bulletins/{region}/{id}/{YYYYMMDD}B.pdf` | ourladyofsorrows.com |
| 3 | WordPress self-hosted (custom naming) | WordPress | `wp-content/uploads/YYYY/MM/YYMMDD*.pdf` | myblessedsacrament.org |
| 4 | Squarespace self-hosted | Squarespace | `{domain}/s/YYYYMMDD-Web.pdf` | holyrosarybirmingham.org |
| 5 | WordPress with descriptive filenames | WordPress | `wp-content/uploads/YYYY/MM/Final-Copy-{Month}-{Day}-{Year}.pdf` | stpeterstpaul.com |
| 6 | Single latest bulletin link | WordPress | `wp-content/uploads/YYYY/MM/{Month}-{Day}-{Year}.pdf` | olfbirmingham.org |
| 7 | Drupal/custom CMS | Drupal/custom | `sites/{name}/files/uploads/bulletins/{desc}.pdf` | queenofangels.org |

**Discovery approach:**
1. Try common bulletin URL paths (`/bulletin`, `/bulletins`, `/home/downloads`, etc.)
2. Scan homepage for links containing "bulletin" keyword
3. Check for LPi/ParishesOnline.com embeds (very common third-party service)
4. Extract all PDF links from discovered bulletin pages

**Name extraction categories + confidence scoring:**
Confidence reflects whether this is likely a **real person connected to the church** (parishioner, staff, or community member).

| Category | Confidence | Description |
|----------|-----------|-------------|
| `clergy_staff` | **high** | Pastor, Parochial Vicar, Deacon, staff listings (Fr./Rev./Msgr. patterns) |
| `mass_intention` | **high** | "For the repose of...", "Special intentions of..." — real people (living or deceased) |
| `prayer_list` | **high** | Sick/homebound lists, prayer requests — real parishioners |
| `ministry_contextual` | **medium** | Names near ministry keywords (lector, usher, cantor, committee) — likely real but looser match |

**Output CSV columns (full provenance):**
| Column | Description |
|--------|-------------|
| `church_name` | Name of the church |
| `church_slug` | CatholicIndex unique identifier |
| `church_url` | Church website URL |
| `pdf_file` | Local filename of downloaded PDF |
| `pdf_url` | Original download URL (source link for verification) |
| `pdf_date` | Date extracted from PDF filename (YYYY-MM-DD) |
| `person_name` | Extracted name |
| `category` | Extraction category (clergy_staff, mass_intention, prayer_list, ministry_contextual) |
| `confidence` | Confidence flag: high, medium, or low |
| `context` | Surrounding text snippet for verification |

**False positive handling:** Maintained blocklist of common non-name phrases (Holy Spirit, Sacred Heart, etc.) and non-name words (Church, Parish, Sunday, etc.). Confidence flag allows downstream filtering — "high" names are very likely real people, "low" names may be false positives. Names will ultimately be matched against a known name list, so recall is prioritized over precision.

**Initial run results (3 PDFs/church cap):**

| Metric | Arizona | Georgia |
|--------|---------|---------|
| Total churches processed | 122 | 236 |
| Churches with bulletin page | 72 (59%) | 140 (59%) |
| Churches with downloadable PDFs | 35 (28%) | ~40 (~17%) |
| Total name extractions | 1,693 | 2,049 |
| **Total unique names** | **960** | **1,162** |
| Churches with extracted names | 27 | 34 |
| Avg unique names per church | 41.1 | 43.5 |
| Median unique names per church | 25.0 | 33.0 |

**Full run (all bulletins per church) in progress** — re-running with MAX_PDFS=100 to capture every bulletin on each church site, not just the 3 most recent. Church bulletin archives typically contain 20–52+ weeks of PDFs.

**Dependencies:** `requests`, `beautifulsoup4`, `lxml`, `pdfplumber`
**Note:** `spacy` NER is incompatible with Python 3.14. Using regex-based name extraction instead, which is more targeted for church bulletin patterns.

**Database tables added:** `bulletin_source`, `bulletin_pdf`, `bulletin_name` (with `is_suspect` flag for possible false positives)
**Database views added:** `v_bulletin_summary` (state-level stats), `v_bulletin_names_detail` (full provenance per name), `v_bulletin_church_stats` (per-church stats with avg/median)

**Status (2026-02-22):** Re-running full pipeline on Arizona and Georgia with all bulletins. Wisconsin and Pennsylvania pending URL resolution completion.

### Phase 8: Docker / Containerization
**Goal:** Containerize both the church scrape pipeline and the bulletin/PDF pipeline as separate Docker services sharing the same data volume.

**Two containers, one data source:**
| Container | Purpose | Key Scripts |
|-----------|---------|-------------|
| `church-scraper` | Scrape churches, mass times, addresses, URLs from CatholicIndex | `run_statewide.py`, `run_resolve_urls.py`, `run_dedup_cleanup.py`, `run_parse_addresses.py` |
| `bulletin-scraper` | Discover bulletins, download PDFs, extract text + names | `run_bulletin_scraper.py` |

**Shared data volume:** Both containers mount the same `data/` directory so the bulletin scraper reads church URLs produced by the church scraper.

**Status:** Planned — not yet implemented.

### Phase 9: Ongoing Maintenance & Future Enhancements
- [ ] Monitor source sites for structural changes
- [ ] Add new data sources as they emerge
- [ ] Potential: other denominations, mobile-friendly web lookup

---

## National Scale Reference Numbers

| Metric | Approximate Count |
|--------|-------------------|
| US Catholic parishes | ~17,000 |
| US dioceses/archdioceses | 196 |
| US states + DC + territories | 56 |
| **Our scraped churches (deduplicated)** | **23,046** |
| Our scraped churches (before dedup) | 31,972 |
| Cross-state duplicates removed | 8,723 |
| Base-slug duplicates removed | 203 |
| Phone coverage | 98% of churches |
| Website coverage | 100% of churches |
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
| Address parser | `src/parsers/address_parser.py` | Token-based street segmentation (43 tests) |
| Testing | `pytest` (109 tests passing) | RSC extractor (22) + smoke (3) + ETL transformers (41) + address parser (43) |
| US city data | 29,880 cities across 52 states | Source: github.com/kelvins/US-Cities-Database |
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
│   └── settings.py                        # Central config: paths, URLs, target communities, statewide settings
│
├── database/
│   └── schema.sql                         # PostgreSQL schema (11 lookup + 8 entity tables + 4 views)
│
├── src/
│   ├── __init__.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   └── catholic_index.py              # CatholicIndex scraper (discover + detail, all 50 states)
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── rsc_extractor.py               # RSC flight payload JSON extractor
│   │   └── address_parser.py              # Token-based street address segmentation
│   ├── etl/
│   │   ├── __init__.py
│   │   ├── transformers.py                # Value mappings: CatholicIndex → DB codes
│   │   └── csv_generator.py               # Shared CSV generation (services + dated services)
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
│   ├── test_transformers.py               # 41 ETL transformer tests
│   └── test_address_parser.py             # 43 address parser tests
│
├── data/
│   ├── city_lists/
│   │   ├── us_cities.csv                  # 29,880 US cities (all states) — source data
│   │   └── ohio_cities.csv                # 1,077 Ohio cities (filtered subset)
│   ├── churches/
│   │   ├── master_church_list.csv         # 79 unique churches (Phase 1 — 16 communities)
│   │   ├── canal_winchester.json          # Per-community church lists (16 files)
│   │   ├── {state}/                       # Statewide church lists (created per state)
│   │   │   ├── master_church_list.csv
│   │   │   ├── discovery_progress.json
│   │   │   └── detail_progress.json
│   │   └── ...
│   └── output/
│       ├── all_services.csv               # 1,203 service rows (Phase 2 — 79 churches)
│       ├── all_churches_detail.json        # Raw JSON (Phase 2 — 79 churches)
│       ├── dated_services.csv             # 1,774 dated instances (Phase 3 — next 2 weeks)
│       └── {state}/                       # Statewide output (created per state)
│           ├── all_services.csv
│           ├── dated_services.csv
│           ├── church_details.jsonl
│           └── parsed_addresses.csv       # Segmented address fields (address parser)
│
├── logs/                                  # Auto-generated log files (scrape_YYYY-MM-DD.log)
│
├── run_discovery.py                       # Phase 1: discover churches for 16 target communities
├── run_scrape_all.py                      # Phase 2: full scrape of all 79 discovered churches
├── run_scrape_sample.py                   # Dev tool: sample scrape of 10 churches
├── run_statewide.py                       # Phase 5: statewide runner (any/all US states)
├── run_parse_addresses.py                 # Address parser CLI: parse JSONL → segmented CSV
├── run_all_states.py                      # Batch runner: scrape + parse + commit for all remaining states
├── run_dedup_cleanup.py                   # Phase 5.5: remove cross-state & base-slug duplicates
├── run_resolve_urls.py                    # Resolve CatholicIndex redirect URLs to actual website URLs
├── run_bulletin_scraper.py                # Phase 7: bulletin discovery + PDF download + name extraction
└── auto_commit_progress.sh               # Cron-style script to commit URL resolver progress every 2 hours
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
7. **TypeError in sorting (NoneType comparison):** `run_statewide.py` line 299 and `run_parse_addresses.py` line 182 crashed when church city/name was `None`. `dict.get("city", "")` returns `None` when key exists with `None` value. **Fixed:** Changed to `c.get("city") or ""` pattern.
8. **Cross-state duplicates (8,723 records):** CatholicIndex's 25-mile radius API caused border-area churches to appear in neighboring state directories. **Fixed:** `run_dedup_cleanup.py` removes records whose slug state doesn't match the directory.
9. **Base-slug duplicates (203 records):** CatholicIndex listed some churches twice with slightly different names but same base slug. **Fixed:** `run_dedup_cleanup.py` keeps the most recently updated variant per base slug.
10. **CatholicIndex redirect URLs:** Website field contained `/api/out?...` redirect links, not actual URLs. The endpoint serves an interstitial HTML page with `window.location.href` in JavaScript. **Fixed:** `run_resolve_urls.py` fetches the interstitial page and parses the actual URL via regex.
11. **JSONL truncation with --limit flag:** `run_resolve_urls.py --limit 10` originally rewrote the JSONL with only the limited records, truncating the full file. **Fixed:** Separated `all_records` from `records` for the rewrite.

---

## Git Commit History

| Commit | Message | Date |
|--------|---------|------|
| `554a30b` | Initial project scaffolding for Catholic Mass Times Scraper | 2026-02-20 |
| `6b127df` | Add Phase 1 church discovery: 79 churches found across 16 Ohio communities | 2026-02-20 |
| `4a09b29` | Fix RSC extractor JSON decoding and add sample scrape script | 2026-02-20 |
| `8e0f117` | Add maximally normalized PostgreSQL schema and ETL transformers | 2026-02-20 |
| `1569b54` | Update project plan with complete findings, schema docs, and pastor data research | 2026-02-20 |
| `f63a0dd` | Add full scrape of all 79 churches with dated service output | 2026-02-21 |
| `7b741bc` | Add statewide scraping infrastructure for all US cities | 2026-02-21 |
| `357736d` | Update project plan for nationwide statewide expansion | 2026-02-21 |
| `f7c545f` | Add Ohio statewide scrape results: 1,239 churches, 13,771 services | 2026-02-21 |
| `d467a8d` | Add address parser with segmented street fields and Ohio parsed CSV | 2026-02-21 |
| `d81f0c4` | Add Texas statewide scrape: 1,597 churches, 19,954 services | 2026-02-21 |
| `aad8dd0` | Add Alabama statewide scrape: 248 churches, 2,903 services | 2026-02-21 |
| `fdb9597` | Add Arkansas statewide scrape | 2026-02-21 |
| `c154004` | Add California statewide scrape | 2026-02-21 |
| `75b8231` | Add Colorado statewide scrape | 2026-02-21 |
| `ac7924e` | Add Connecticut statewide scrape | 2026-02-21 |
| `b9f7b76` | Add Delaware statewide scrape | 2026-02-21 |
| `64f78be` | Add Georgia statewide scrape | 2026-02-21 |
| `3a5e294` | Add Hawaii statewide scrape | 2026-02-21 |
| `a4b415c` | Add Idaho statewide scrape | 2026-02-21 |
| `39bf11e` | Add Illinois statewide scrape | 2026-02-21 |
| `164313e` | Add Indiana statewide scrape | 2026-02-21 |
| `124d504` | Add Iowa statewide scrape | 2026-02-21 |
| *(batch continues...)* | Kansas through Wyoming running via `run_all_states.py` | 2026-02-21 |

---

## Editorial Considerations

- **Holy Week / Christmas / Holy Days:** Special schedules published by parishes 2–4 weeks before events. Scraper flags these dates so editorial can follow up.
- **Language masses:** Some parishes offer masses in Spanish, Vietnamese, etc. Language field included in schema and output.
- **Pastor/clergy attribution:** Not yet available from CatholicIndex. Schema includes `clergy` + `clergy_assignment` tables for when data source is chosen.
- **Disclaimer:** "Mass times are subject to change. Please verify with your parish before attending."
- **Frequency decision needed:** Does the newspaper publish mass times every issue, or just monthly?
- **Grouping decision needed:** Group by church or by date?

---

## How to Resume / Re-Run

### If the batch was interrupted (machine crash, etc.)
```bash
# The batch runner skips already-completed states automatically:
python run_all_states.py

# Or run individual states manually:
python run_statewide.py kansas
python run_parse_addresses.py kansas
git add data/churches/kansas/ data/output/kansas/
git commit -m "Add Kansas statewide scrape"
```

### Re-run states that had issues (DC, Florida, Alaska, Arizona)
```bash
# Delete their incomplete data first, then re-run:
rm -rf data/churches/florida data/output/florida
python run_statewide.py florida
python run_parse_addresses.py florida
```

### Parse addresses for all completed states at once
```bash
python run_parse_addresses.py all
```

### Run deduplication cleanup (after any re-scrape)
```bash
# Dry run first — see what would be removed:
python run_dedup_cleanup.py

# Apply the cleanup:
python run_dedup_cleanup.py --apply

# Regenerate parsed addresses after cleanup:
python run_parse_addresses.py all
```

### Resolve website URLs (after cleanup)
```bash
# Resolve CatholicIndex redirect URLs to actual website URLs:
python run_resolve_urls.py all

# Or individual states:
python run_resolve_urls.py ohio --resume
```

### Run bulletin scraper (after URL resolution is complete for target states)
```bash
# Full pipeline on specific states:
python run_bulletin_scraper.py all arizona georgia pennsylvania wisconsin

# Test with a small sample first:
python run_bulletin_scraper.py all arizona --limit 10

# Resume if interrupted:
python run_bulletin_scraper.py all arizona --resume

# Run individual phases:
python run_bulletin_scraper.py discover arizona    # Find bulletin pages
python run_bulletin_scraper.py download arizona    # Download PDFs
python run_bulletin_scraper.py extract arizona     # Extract text + names
```

### Weekly refresh (once all states are complete)
```bash
# Re-scrape all states with fresh data:
python run_all_states.py
# Run dedup cleanup:
python run_dedup_cleanup.py --apply
# Regenerate addresses:
python run_parse_addresses.py all
# Resolve URLs:
python run_resolve_urls.py all
# Run bulletin scraper:
python run_bulletin_scraper.py all all
```

---

## Next Steps (Immediate)

1. ~~**Run Ohio statewide scrape**~~ ✅ DONE — 1,239 churches, 13,771 services
2. ~~**Run Texas statewide scrape**~~ ✅ DONE — 1,597 churches, 19,954 services
3. ~~**Build address parser**~~ ✅ DONE — token-based parser with 43 tests
4. ~~**Finish remaining states**~~ ✅ DONE — all 50 states scraped and committed
5. ~~**Dedup & cross-state cleanup**~~ ✅ DONE — 31,972 → 23,046 unique churches
6. **Resolve all website URLs** — `python run_resolve_urls.py all` on cleaned 23,046 records
7. **Generate national coverage report** — How many parishes per state, coverage vs known parish counts
8. **Set up PostgreSQL** and run `database/schema.sql` to create the production database
9. **Build database loader** — Script to INSERT transformed data into PostgreSQL using the ETL transformers
10. **Set up weekly automation** — Automated Sunday re-scrape of all 50 states (GitHub Actions or Task Scheduler), auto-commit + push results
10. **Choose a pastor/clergy data source** — CatholicParishDirectory.com (commercial) or scrape individual parish websites
11. **Generate sample newspaper-ready output** for one publication week and review with editorial team
