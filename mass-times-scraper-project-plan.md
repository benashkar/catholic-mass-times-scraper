# Catholic Mass Times Scraper — Project Plan

## Project Overview

**Project:** Automated Catholic Mass Times Scraper for CR Community News  
**Owner:** Ben Ashkar (Healthy Analytics)  
**Scope:** Scrape, parse, and publish weekly Catholic mass times — starting with 15 Ohio communities, scaling to all US cities  
**Final Output:** Date-specific mass time listings for newspaper publication (story + table format)  
**Long-Term Vision:** A comprehensive, continuously updated database of Catholic mass times for every parish in the United States, powering CR Community News editions in any market

---

## Target Communities

| # | Community | County/Area |
|---|-----------|-------------|
| 1 | Canal Winchester | Franklin |
| 2 | Obetz | Franklin |
| 3 | Hamilton Township | Franklin |
| 4 | Lithopolis | Fairfield |
| 5 | Lockbourne | Franklin |
| 6 | Groveport | Franklin |
| 7 | Madison Township | Franklin |
| 8 | West Columbus | Franklin |
| 9 | Lincoln Village | Franklin |
| 10 | Prairie Township | Franklin |
| 11 | Westgate | Franklin |
| 12 | Galloway | Franklin |
| 13 | Hilliard | Franklin |
| 14 | Grove City | Franklin |
| 15 | Urbancrest | Franklin |
| 16 | Commercial Point | Pickaway |

> **Note:** Many smaller communities (Obetz, Lockbourne, Urbancrest, Lincoln Village, Westgate) may not have their own Catholic church. The scraper needs to search by zip code or radius to find nearest serving parishes.

---

## Data Sources (Priority Order)

### Tier 1: CatholicIndex.org (Primary)
- **Why:** Server-side rendered HTML — mass times are directly in page source (no JS rendering needed)
- **Coverage:** 20,000+ parishes, updated weekly with automatic change detection
- **Data available:** Church name, address, phone, website, day-by-day mass times, confession times, adoration hours, special events, monthly services
- **URL pattern:** `catholicindex.org/churches/us-oh-{city}-{church-name}-{hash}`
- **City pages:** `catholicindex.org/mass-times/{city}-ohio`
- **Search page:** `catholicindex.org/search`
- **Limitations:** City listing pages may use some JS rendering for church URLs; individual church pages are fully server-rendered

### Tier 2: DiscoverMass.com (Backup / Cross-Reference)
- **Why:** Also server-side rendered HTML; partners with MassTimes.org for data
- **URL pattern:** `discovermass.com/church/{slug}-{city}-{state}/`
- **Data available:** Mass times, confession, adoration, directions
- **Limitations:** Slightly less actively maintained than CatholicIndex

### Tier 3: MassTimes.org (Largest DB, Harder to Scrape)
- **Coverage:** 117,000 churches worldwide — most comprehensive
- **Problem:** Entirely JavaScript-rendered (AngularJS SPA). Requires headless browser (Playwright/Puppeteer) or reverse-engineering internal API endpoints
- **Use case:** Only if Tiers 1 and 2 have gaps

### Not Recommended
- **MassTime.us** — JS-rendered, smaller database
- **Diocese of Columbus (columbuscatholic.org)** — No centralized mass times; links to individual parish sites only
- **CatholicDirectory.com** — Less structured data

---

## Data Schema

### Church Record

| Field | Description | Example |
|-------|-------------|---------|
| `church_name` | Parish name | Our Lady of Perpetual Help |
| `church_type` | Rite/type | Roman Catholic |
| `rite` | Liturgical rite | Latin |
| `diocese` | Diocese name | Diocese of Columbus |
| `street_number` | House/building number | 3730 |
| `street_direction_prefix` | Directional prefix (N, S, E, W) | |
| `street_name` | Street name | Broadway |
| `street_type` | Street type (Rd, Ave, St, etc.) | |
| `street_direction_suffix` | Directional suffix | NW |
| `city` | City | Grove City |
| `state` | State abbreviation | OH |
| `zip_code` | ZIP code | 43123 |
| `latitude` | Latitude | 39.8812 |
| `longitude` | Longitude | -83.0868 |
| `phone` | Phone number | (614) 875-3322 |
| `website` | Parish website URL | |
| `source_url` | URL scraped from | |
| `last_scraped` | Timestamp of last scrape | 2026-02-20T12:00:00Z |

### Mass Time Record

| Field | Description | Example |
|-------|-------------|---------|
| `church_name` | FK to church | Our Lady of Perpetual Help |
| `day_of_week` | Recurring day | Saturday |
| `time` | Mass time | 4:00 PM |
| `mass_type` | Type (Vigil, Regular, Holy Day, etc.) | Vigil |
| `language` | Language if noted | English |
| `is_recurring` | Weekly recurring vs. one-time | true |
| `specific_date` | For special events only | null |
| `notes` | Any qualifiers | |

### Confession Time Record

| Field | Description | Example |
|-------|-------------|---------|
| `church_name` | FK to church | Our Lady of Perpetual Help |
| `day_of_week` | Day | Saturday |
| `start_time` | Start | 3:00 PM |
| `end_time` | End | 3:45 PM |
| `notes` | Qualifiers | Or by appointment |

### Special Event Record

| Field | Description | Example |
|-------|-------------|---------|
| `church_name` | FK to church | Our Lady of Perpetual Help |
| `event_date` | Specific date | 2026-04-03 |
| `event_name` | Event | Good Friday Service |
| `time` | Time | 3:00 PM |
| `description` | Details | Passion of the Lord |

---

## Phase Plan

### Phase 1: Church Discovery & Mapping
**Goal:** Build a complete list of Catholic churches serving each of the 15 communities.

- [ ] For each community, determine the relevant ZIP codes and a search radius (some small towns share parishes)
- [ ] Scrape CatholicIndex.org city/search pages to get all church URLs per community
- [ ] Cross-reference with DiscoverMass.com to catch any gaps
- [ ] Cross-reference with Diocese of Columbus parish list for completeness
- [ ] Build a master church list CSV with all address/contact fields
- [ ] Manual QA: verify no major parishes are missing

**Known challenge:** City listing pages on CatholicIndex may require JS rendering for church links. Alternatives:
- Use CatholicIndex search endpoint (inspect with Chrome DevTools / Claude in Chrome)
- Use DiscoverMass.com's predictable URL slug pattern
- Fall back to zip code-based search

### Phase 2: Mass Time Scraper (Recurring Schedules)
**Goal:** Scrape and parse weekly recurring mass times for every church in the master list.  
**Start here:** Unit tests (Improvement 1) and file logging (Improvement 2).

- [ ] Build HTML parser for CatholicIndex individual church pages (BeautifulSoup or similar)
- [ ] **Write unit tests alongside each parser function** (see Improvement 1)
- [ ] **Add logging to all scraper functions** (see Improvement 2)
- [ ] Parse mass times by day of week
- [ ] Parse confession times — **fix range format bug** (e.g., "3:00pm-3:45pm" should be start/end, not two separate times)
- [ ] Parse adoration hours
- [ ] Parse monthly services (First Friday, First Saturday, etc.)
- [ ] Parse any date-specific special events listed
- [ ] Store all data in structured JSON/CSV
- [ ] Build DiscoverMass.com parser as fallback
- [ ] Add `last_updated` tracking per church (CatholicIndex shows schedule update dates)

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
**Start here:** Dockerization (Improvement 5) and Slack/email alerts (Improvement 4).

- [ ] **Dockerize the project** before setting up scheduling (see Improvement 5)
- [ ] **Standard mode (weekly):** Run Sunday night or Monday morning; scrape all recurring schedules; generate date-specific listings for the upcoming publication window
- [ ] **Change detection:** Compare current scrape to previous; flag any churches that updated their schedule
- [ ] **Special events mode:** Pull any date-specific events from CatholicIndex "Special Events" section
- [ ] **Holy Day alerts:** Flag upcoming Holy Days of Obligation and major liturgical events (Holy Week, Christmas, Ash Wednesday) so editorial team can follow up with parishes for special schedules
- [ ] **Set up Slack/email alerts** for scrape failures and anomalies (see Improvement 4)
- [ ] Store scrape history for audit trail

### Phase 5: Ohio Statewide Expansion
**Goal:** Expand coverage to all Catholic parishes across Ohio.  
**Start here:** Web dashboard (Improvement 3) — useful once data exceeds what CSVs can handle comfortably.

- [ ] Compile a master list of all Ohio cities/towns/CDPs (Census Bureau data or USPS city list)
- [ ] Map all Ohio ZIP codes to their serving parishes
- [ ] Run the scraper against all Ohio communities using CatholicIndex state-level pages
- [ ] Cross-reference with Diocese directories (Ohio has 6 dioceses: Columbus, Cincinnati, Cleveland, Toledo, Youngstown, Steubenville)
- [ ] Validate coverage: compare total parish count against the Official Catholic Directory (OCD) for Ohio
- [ ] Optimize scraper performance for larger runs (batching, rate limiting, caching)
- [ ] Move from flat files to PostgreSQL for storage at this scale

### Phase 6: National Expansion — Region by Region
**Goal:** Scale to all ~17,000 Catholic parishes in the United States.

**Sub-phase 6a: Data Architecture for National Scale**
- [ ] Design PostgreSQL schema to handle ~17,000 parishes, ~50,000+ mass time records
- [ ] Add geographic hierarchy: state → diocese → deanery → parish
- [ ] Integrate USPS city/ZIP reference data for comprehensive city-to-parish mapping
- [ ] Build incremental scrape logic (only re-scrape churches whose schedules may have changed, based on CatholicIndex's `last_updated` field)
- [ ] Implement rate limiting and polite scraping (respect robots.txt, add delays, rotate user agents if needed)

**Sub-phase 6b: Diocese-by-Diocese Rollout**
- [ ] Compile master list of all 196 US dioceses/archdioceses and their geographic boundaries
- [ ] Prioritize rollout order (e.g., by CR Community News expansion markets, or by diocese size)
- [ ] For each diocese:
  - Scrape CatholicIndex for all parishes in the diocese's territory
  - Cross-reference with DiscoverMass.com for gaps
  - Cross-reference with the diocese's own parish directory (most dioceses publish one online)
  - Validate parish count against OCD or CARA (Center for Applied Research in the Apostolate) data
- [ ] Track coverage metrics: % of known parishes scraped per diocese, per state

**Sub-phase 6c: Data Quality & Enrichment**
- [ ] Build automated data quality checks:
  - Parishes with no mass times listed
  - Parishes with suspiciously outdated schedules (no update in 6+ months)
  - Duplicate parish detection (same church listed under slightly different names/addresses)
- [ ] Enrich with additional data sources:
  - USCCB (US Conference of Catholic Bishops) parish data
  - Google Places API for address verification and coordinates
  - Individual diocese websites for parishes missing from aggregators
- [ ] Build a "confidence score" per parish (how recently verified, how many sources agree)

**Sub-phase 6d: Automation at Scale**
- [ ] Scheduled national scrape: full refresh monthly, incremental weekly
- [ ] Change detection alerts: flag parishes that update their schedules
- [ ] Dashboard or report showing coverage stats by state/diocese
- [ ] API or export system so any CR Community News edition can pull mass times for its market

### Phase 7: Ongoing Maintenance & Future Enhancements
**Goal:** Keep the national database accurate and explore additional features.

- [ ] Monitor source sites for structural changes (HTML layout changes that break parsers)
- [ ] Add support for additional data sources as they emerge
- [ ] Consider contributing back to open data efforts (Parish.io, OpenCatholic)
- [ ] Potential enhancements:
  - Other denominations beyond Catholic (Protestant, Orthodox, etc.)
  - Bilingual/multilingual mass filtering
  - Mobile-friendly web lookup for readers
  - Integration with parish bulletin scraping for Holy Day / special event schedules
  - Mapping/geospatial features (nearest parish finder)

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
| Scraping | Python (`requests` + `BeautifulSoup4`) | Primary for server-rendered pages |
| JS-rendered pages (if needed) | Playwright or Puppeteer | For MassTimes.org or stubborn sites |
| Address parsing | Custom regex parser (from POC) | |
| Data storage (Phase 1–4) | JSON + CSV flat files | 15 communities, ~50 parishes |
| Data storage (Phase 5+) | PostgreSQL | ~17,000 parishes nationally |
| Geographic reference data | Census Bureau, USPS ZIP files | City/ZIP to parish mapping |
| Scheduling | Cron job or GitHub Actions | Weekly incremental, monthly full |
| Orchestration | Claude Code | |
| Rate limiting | Custom (respect robots.txt, 1-2 req/sec) | Critical for national-scale scraping |
| Output generation | Python (CSV, Markdown, or direct layout format) | Per-market newspaper listings |

## Coding Standards — Junior Developer Friendly

> **Important:** This project will be managed by a junior developer who is new to Python. All code must be written with extensive documentation and clear structure to support learning and independent troubleshooting.

### Comment Requirements (Apply to ALL Code)

- **Every file** must start with a module-level docstring explaining what the file does, what inputs it expects, and what outputs it produces
- **Every function** must have a docstring explaining: what it does, what each parameter means, what it returns, and an example usage where helpful
- **Every non-obvious line or block** must have an inline comment explaining *why* it does what it does, not just *what* it does
- **Complex logic** (regex, parsing, data transformations) must have step-by-step comments walking through the logic
- Use **descriptive variable names** — `church_address_parts` not `cap`, `mass_time_str` not `mts`
- Include **"WHY" comments** for any design decisions — e.g., `# We use semicolons here instead of commas because commas break CSV column alignment`
- Add **WARNING comments** for common pitfalls — e.g., `# WARNING: This URL pattern changes if CatholicIndex redesigns their site`

### Code Example — What Good Looks Like

```python
"""
scrape_church_page.py

Scrapes a single church page from CatholicIndex.org and extracts
mass times, confession times, and address information.

Input:  A CatholicIndex church page URL (string)
Output: A dictionary containing church details and schedule data

Usage:
    python scrape_church_page.py https://catholicindex.org/churches/us-oh-grove-city-...
"""

def parse_confession_time_range(time_string: str) -> dict:
    """
    Parse a confession time range string into start and end times.

    CatholicIndex lists confession times as ranges like "3:00pm-3:45pm".
    This function splits that into separate start and end time values.

    Args:
        time_string: A time range string, e.g., "3:00pm-3:45pm"

    Returns:
        A dictionary with 'start_time' and 'end_time' keys.
        Example: {'start_time': '3:00 PM', 'end_time': '3:45 PM'}

    Raises:
        ValueError: If the time string doesn't contain a valid range.
    """
    # Split on the dash to get start and end times
    # WARNING: Some entries use an en-dash (–) instead of a hyphen (-)
    # so we check for both characters
    ...
```

### File & Folder Naming
- Use **snake_case** for all Python files: `scrape_churches.py`, `parse_address.py`
- Group related files into folders with a `README.md` in each explaining the folder's purpose
- Keep a top-level `CONTRIBUTING.md` with setup instructions and development workflow

---

## Additional Improvements (Build Incrementally Across Phases)

These improvements make the project more robust, easier to maintain, and accessible to a junior developer working independently. Each one can be tackled as a standalone task.

### Improvement 1: Unit Tests
**Why:** Lets the junior dev test parsing and transformation functions without running the full scraper (no network calls, no waiting).
**When:** Start in Phase 2 alongside the parser code.

- [ ] Use `pytest` as the test framework (simpler than `unittest` for beginners)
- [ ] Create a `tests/` folder with one test file per module (e.g., `test_parse_address.py`, `test_parse_mass_times.py`, `test_parse_confession.py`)
- [ ] Write tests for:
  - Address parser: known good addresses, edge cases (directional prefixes/suffixes, long street names, PO Boxes)
  - Mass time parser: single times, multiple times, vigil masses, different day formats
  - Confession time range parser: hyphen ranges, en-dash ranges, single times, "by appointment" text
  - Date generation: correct mapping of day-of-week to specific dates, edge cases around month boundaries
- [ ] Include **sample HTML fixtures** — save real HTML snippets from CatholicIndex/DiscoverMass into `tests/fixtures/` so tests can parse them without hitting the live site
- [ ] Add a `make test` or `pytest` command to the README so any developer can run tests immediately
- [ ] Target: 80%+ code coverage on parser/transformation functions (don't need to test the network scraping itself)

### Improvement 2: Logging to Files
**Why:** When a weekly scrape runs at 2 AM and something breaks, logs are the only way to figure out what happened.
**When:** Start in Phase 2, require for Phase 4 (automation).

- [ ] Use Python's built-in `logging` module (not `print` statements)
- [ ] Configure two log outputs:
  - **Console:** INFO level and above (so the dev can watch progress in real time)
  - **File:** DEBUG level, written to `logs/scrape_YYYY-MM-DD.log` (full detail for troubleshooting)
- [ ] Log the following events:
  - Scrape start/end with timestamp and city name
  - Each church page fetched (URL, HTTP status code, response time)
  - Parse successes and failures per church
  - Any data that couldn't be parsed (log the raw text so we can fix the parser)
  - Summary at end: "Scraped 47 churches, 45 successful, 2 failed"
- [ ] Rotate log files (keep last 30 days, delete older ones)
- [ ] Add a `# HOW TO READ THESE LOGS` section at the top of the logging config file

### Improvement 3: Simple Web Dashboard
**Why:** Lets the junior dev (and the editorial team) view scraped data without writing SQL queries or opening CSV files.
**When:** Phase 4 or Phase 5 — once there's enough data to make it worthwhile.

- [ ] Use **Flask** (lightweight, beginner-friendly — the team already has Flask experience from the Medicaid project)
- [ ] Pages to build:
  - **Home:** Summary stats (total churches, last scrape date, next scheduled scrape)
  - **Churches list:** Searchable/filterable table of all churches with city, type, last updated
  - **Church detail:** Individual church page showing all mass times, confession, contact info
  - **Scrape log:** View recent scrape runs, successes/failures
  - **Export:** Button to download current data as CSV for the newspaper
- [ ] Use a simple CSS framework (Bootstrap or Pico CSS) — no React needed
- [ ] Include `README.md` with screenshots showing what each page looks like
- [ ] Deploy to Render.com (consistent with existing infrastructure)

### Improvement 4: Slack/Email Alerts
**Why:** If the weekly scrape fails at 2 AM Sunday, the team needs to know Monday morning — not when the newspaper deadline hits.
**When:** Phase 4 (automation).

- [ ] Start with **email alerts** (simpler, no external dependencies):
  - Use Python's `smtplib` with Gmail or the team's email provider
  - Send alert on: scrape failure, scrape completing with errors (>10% failure rate), source site structure change detected
  - Include: what failed, error message, link to log file
- [ ] Optional: Add **Slack webhook** integration:
  - Post to a `#mass-times-scraper` channel
  - Color-coded: green for success, yellow for warnings, red for failures
  - Include summary stats in the message
- [ ] All alert configuration in a single `config.py` or `.env` file (no hardcoded emails/webhooks in code)

### Improvement 5: Dockerize the Project
**Why:** "It works on my machine" is the #1 junior developer problem. Docker eliminates environment setup issues entirely.
**When:** Phase 3 or Phase 4 — once the core scraper is stable.

- [ ] Create a `Dockerfile` with:
  - Python 3.11+ base image
  - All pip dependencies installed from `requirements.txt`
  - Playwright browsers pre-installed (if needed for Tier 3 scraping)
  - Working directory and entry points configured
- [ ] Create a `docker-compose.yml` with:
  - Scraper service
  - PostgreSQL database (for Phase 5+)
  - Flask dashboard (for Improvement 3)
  - Volumes for persistent data and logs
- [ ] Add extensive comments in both Docker files explaining every line
- [ ] README section: "Getting Started with Docker" — step by step:
  1. Install Docker Desktop
  2. Clone the repo
  3. Run `docker-compose up`
  4. Open `localhost:5000` for dashboard
- [ ] Include a `Makefile` with common commands:
  ```
  make build        # Build Docker containers
  make up           # Start everything
  make scrape       # Run a one-time scrape
  make test         # Run unit tests
  make logs         # View recent logs
  make shell        # Open a bash shell in the container
  ```

---

1. **Confession time range parsing:** "3:00pm-3:45pm" is being split into two separate times instead of a start/end range. Fix: detect dash-separated time ranges before splitting.
2. **CSV comma conflicts:** Fields containing commas (like `address_raw`) break column alignment in spreadsheet viewers. Fix: either drop `address_raw` (redundant) or use pipe/tab delimiters. **Status: Fixed in POC v2.**
3. **Mass time delimiter:** Use semicolons (`;`) instead of commas to separate multiple times within a single cell. **Status: Fixed in POC v2.**
4. **Church discovery for small communities:** Need zip-code or radius-based search, not just city name matching.

---

## Editorial Considerations

- **Holy Week / Christmas / Holy Days:** These special schedules are published by individual parishes 2–4 weeks before the event, not in the aggregator sites. The scraper should flag these dates so the editorial team can contact parish offices or scrape parish bulletin pages directly.
- **Language masses:** Some parishes offer masses in Spanish, Vietnamese, etc. Include language field in output.
- **Disclaimer:** "Mass times are subject to change. Please verify with your parish before attending." — standard disclaimer for the newspaper listing.
- **Frequency decision needed:** Does the newspaper publish mass times every issue, or just monthly? This affects the date range for the generation layer.
- **Grouping decision needed:** Group by church (all of St. Mary's times together) or by date (everything happening Saturday March 7th)?

---

## Next Steps (Immediate)

1. **Use Claude in Chrome** to inspect CatholicIndex.org's network requests and find their search/API endpoint structure
2. **Map all 15 Ohio communities** to their serving parishes (church discovery)
3. **Refine the scraper** — fix confession parsing, add date generation
4. **Generate a sample newspaper-ready output** for one publication week across all 15 communities
5. **Review with editorial team** for format/layout preferences
6. **Design the PostgreSQL schema** early — even if Phase 1–4 uses flat files, having the schema ready avoids rework when scaling to Ohio statewide and then nationally
