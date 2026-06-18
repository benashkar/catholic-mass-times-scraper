# Church Scrapes — Project Plan

_Last updated: 2026-06-18 (mass-times source pivot: CatholicIndex → DiscoverMass)._

## ⚠️ 2026-06-18 — MASS-TIMES SOURCE PIVOT (CatholicIndex dead → DiscoverMass)

**CatholicIndex.org is unscrapeable.** It went behind a hard Cloudflare **Managed Challenge**
(`cf-mitigated: challenge`) on all content pages (`/churches/*`, `/mass-times/*`) ~2026-04. The
daily/weekly scrape has been a **silent no-op since ~2026-04-22** (exit 0, 0 rows): `scrape_log`
shows `scrape=FAIL` every run since 2026-04-02, and 100% of churches were >30d stale. Nothing
in-house beats the challenge (requests, curl_cffi, Playwright-headless, undetected-chromedriver-
headed, residential proxy all 403). Only a paid scraping-browser (BrightData) would — not pursued.

**New source = DiscoverMass.com** (open, plain `requests` 200, ~20,283 parishes via WP sitemap):
- `src/scrapers/discover_mass.py` — `enumerate_parishes(state)` (sitemap, slug-suffix state filter)
  + `parse_parish()` (mirrors `catholic_index.scrape_church_detail` output).
- `discovermass_to_db99.py --state XX [--commit]` — match-or-insert into db99. Matches existing
  churches by **geo ≤150m + normalized name/city** (preserves `church_id` + bulletin links),
  inserts net-new, flags ambiguous (skipped). Per-church schedule replace ONLY when DM has ≥1
  service (0-service parishes keep old schedule); per-state stale-service cleanup; explicit commit.
- `run_discovermass_all.py` — loops states (**Cherry Road states first**), idempotent/self-healing.
- **Weekly pipeline Step 1 now calls `run_discovermass_all.py`** (was the dead CatholicIndex
  `run_statewide.py --detail-only`). Bulletins/rescore steps unchanged.
- **Fetch config: DIRECT, NO PROXY, `SCRAPE_DELAY_SECONDS=6` on `church-mass-times-cron`.**
  The 711 residential proxy proved UNRELIABLE for DiscoverMass — a Render one-off job through the
  proxy exited 0 but wrote **0 rows** (every proxied fetch failed; a local re-run also collapsed
  86→2). Direct fetching at ~6s is reliable (verified). `PROXY_URL` was REMOVED from the cron.
  http.py retries cover transient 503s. Slower (~10-15h national) but self-heals across weekly runs.

**VERIFIED end-to-end (2026-06-18):**
- **AR pilot (local commit path):** 86 parishes refreshed + 6 net-new; stale CatholicIndex services
  retired where DM provided a schedule; 1 church (0-service) kept its old schedule by design.
  Merged 2 pre-existing AR duplicate church rows (`scripts/merge_ar_duplicate_churches.py`), all
  bulletin names preserved.
- **ME (Render one-off, DIRECT prod path):** 144 churches refreshed, 131 with active DM services
  (vs 0 through the proxy) — confirms the direct path writes cleanly from Render's datacenter IP.

**Cherry Road geography** is synced from Limpar (source of truth) into db99 `cr_market_shape`
via `scripts/refresh_cherry_road_shapes.py` (96 projects / 845 shapes; 599 cities). Coverage diff:
`scripts/cr_coverage_report.py`. Both built "the right way" — re-run to pick up Limpar changes.

**Status / remaining:** CR-states rollout running on Render now (`run_discovermass_all.py --cr-only`,
direct/6s); states land incrementally (AR + ME done = 2/21 at time of writing). The weekly cron
(Step 1 = DiscoverMass, CR-first) carries the full national sweep forward + keeps it fresh. Some CR
micro-towns have no parish of their own (served by neighbor towns) — a parish-existence limit, not a
coverage bug. Live count: `DB_HOST=10.10.0.8 python scripts/cr_coverage_report.py`.

## Overview
Catholic church mass times, bulletins, and extracted names dashboard.
- **Repo**: benashkar/catholic-mass-times-scraper
- **Database**: `church_scrapes` on db99 (MySQL, us-east-1 Virginia)
- **Dashboard**: Render web service (Virginia, Docker), reads from db99 via AWS Secrets Manager
- **Pipeline**: Two Render cron jobs — daily mass times + weekly bulletins
- **Name Engine**: `benashkar/names_people_matcher` (`C:\Users\cashk\OneDrive\names_people_matcher`)

## Current Status (2026-04-03)

### COMPLETED
1. **Dashboard live** — 50 states, medium+high confidence names, shareable page URLs
2. **SQL rescore** — ref_ssa_names + ref_census_surnames on db99, junk blocklist, lowercase cleanup
3. **Column detection** — Integrated into `run_bulletin_scraper.py` via `src/utils/pdf_columns.py`
4. **Pattern 6 fix** — Changed from 200-char proximity to line-based matching (keyword line + next 5 lines)
5. **Direct-to-db99 mass times** — `scrape_to_db99.py` reads church list from db99, scrapes CatholicIndex, UPSERTs directly
6. **Direct-to-db99 bulletins** — `extract_bulletins_to_db99.py` discovers bulletin PDFs, downloads to memory (BytesIO), extracts text with column-aware pdfplumber, runs 6 regex patterns + NER veto + couple detection, inserts names directly to db99. Fully stateless.
7. **Daily pipeline** — `run_daily_pipeline.py` orchestrates: mass times scrape → bulletin extraction → rescore → health check → redeploy
8. **Multi-model name quality engine** (Phase 1-2 complete):
   - **nameparser** library replaces hand-rolled `parse_name_parts()` — handles "Fr. John M. Smith Jr." correctly
   - **probablepeople** detects couple names ("John & Mary Smith") → splits into two records
   - **NER veto gate** (spaCy en_core_web_lg) — names NER doesn't recognize as PERSON get downgraded to low/suspect
   - **SSA gender data** — male_count, female_count, male_ratio columns for husband+wife detection
   - **Expanded SQL blocklist** — ~100+ additional junk terms
9. **NER rescore of all existing data** (Phase 4 complete):
   - 2,339,934 names checked across 50 states
   - 1,556,276 false positives eliminated (66.5%)
   - ~783,658 quality names remaining on dashboard
   - Ran as 49 individual state jobs on Render (each 10s-30min depending on state size)
10. **Health checks** — 9 automated checks (table counts, confidence distribution, junk rate, known junk, lowercase names, scrape recency, empty names, backfill coverage, recent pipeline runs)
11. **PrivateLink fixed** — VPC endpoint works from Render
12. **Debug endpoint** — `/debug/logs` on dashboard for cron job visibility (Render API lacks job log access)
13. **Fix column mismatch** — `extract_bulletins_to_db99.py` and `rescore_with_ner.py` had wrong column names vs actual db99 schema (written against PG schema, but db99 tables created by `sync_to_db99.py` have different names). Fixed: `source_type`→`discovery_source`, `source_url`→`bulletin_page_url`, `url`→`pdf_url`, `extracted_at`→`text_extracted`, `extracted_context_category`→`category`, `last_scraped_at`→`discovered_at`. Added `title`+`middle_name` to INSERTs. Idempotent `ensure_schema()` auto-adds `role` column on first Render run.
14. **Dashboard: dynamic stats + CSV export + shareable filter URLs** — Stats cards (Total Names, Unique Names) now update live when any filter changes. Confidence filter moved to server-side SQL for correct paginated results. CSV download button exports filtered view. All filters (confidence, city, church, category, search) sync to URL query params for shareable links (e.g. `/bulletin/ohio/?confidence=high&city=Columbus`).
15. **Code quality cleanup** — Fixed SQL injection in rescore watermark (f-string → parameterized query). Consolidated 190 individual blocklist UPDATEs into single query. Consolidated 70 junk first/last word UPDATEs into 2 IN() queries. All cleanup steps scoped to watermark in `--new-only` mode.
16. **Last Updated column on bulletin index** — Shows `MAX(bulletin_pdf.downloaded_at)` per state. Green if within 7 days, red if stale — monitors whether the weekly bulletin pipeline is running.
17. **CSV export limit raised to 500K** — Was 50K, silently truncating large states (IL 253K, WI 64K).
18. **Schema page** (`/schema`) — ERD diagram (Mermaid.js), connection info (Render + local), Python quick start, live table row counts, key views. Full-screen ERD at `/schema/erd.html`. Standalone `docs/erd.html` committed to repo per global rules.
19. **Weekly cron deduplication** — Added `--bulletins-only` flag to `run_daily_pipeline.py`. Weekly Tuesday cron now skips mass times (already handled by daily cron), focuses on bulletin PDF collection + NER + rescore.
20. **Self-healing scraper pattern (3 layers)** — Implemented 2026-04-03:
    - **Layer 1 — Inline fallback parsers**: `src/parsers/fallback_parsers.py` with 4 functions (`parse_first_last_from_person_name`, `parse_category_from_context`, `parse_role_from_context`, `parse_title_from_person_name`). Called in `extract_bulletins_to_db99.py` after primary `parse_name_parts()` when fields are empty. Shared constants extracted to `src/parsers/bulletin_constants.py`.
    - **Layer 2 — Auto-backfill**: `backfill_empty_fields.py` re-parses existing DB records with empty critical fields. Runs as Step 2b in `run_daily_pipeline.py` (non-critical, <60s target, 50K row limit).
    - **Layer 3 — Diagnostic agent**: `/health` endpoint upgraded to comprehensive JSON with 9 structured checks. Claude Code scheduled trigger (`trig_013BWLSXWDbY4tFCXo9HiwDB`) runs at 5 AM UTC daily (except Tuesday). Fetches `/health`, diagnoses issues, opens fix PRs for blocklist additions, sends Telegram summary via Biscotcho bot.
    - **Telegram integration**: `src/utils/telegram.py`, env vars set on both Render cron services.
    - **38 tests** in `tests/test_fallback_parsers.py`.

### CURRENT DATA
- 2.6M bulletin_name rows total
- ~784K medium+high confidence (dashboard-visible) — down from ~1.2M after NER cleanup
- 23,046 churches, 280K services
- ref_ssa_names (100K with gender data), ref_census_surnames (162K) on db99

## Render Services

| Service | ID | Type | Plan | Command |
|---------|-----|------|------|---------|
| catholic-church-dashboard | `srv-d6li8dtm5p6s73chuh7g` | web | starter | gunicorn |
| church-mass-times-cron¹ | `crn-d6s8st3uibrs73e7b740` | cron (`0 3 * * 0,1,3,4,5,6`) | **standard** (2GB) | image CMD `python run_weekly_pipeline.py` (no startCommand override; use `--skip-bulletins` for mass-times only). autoDeploy=yes |
| church-weekly-bulletins | `crn-d6s8t02a214c73bt62s0` | cron (Tue 3AM) | standard | `python run_weekly_pipeline.py` |

¹ Render service display name is `church-mass-times-cron` (the original "church-daily-scrape" label is stale). Confirmed 2026-06-14.

## Pipeline Architecture

### Daily (stateless → db99)
`python run_daily_pipeline.py`
1. Scrape mass times from CatholicIndex → UPSERT to db99
2. Extract bulletin names → discover PDFs → download to memory → extract text → NER veto → UPSERT to db99 (with Layer 1 inline fallback parsers)
2b. Backfill empty fields from context (Layer 2 self-healing)
3. Rescore names via SQL (`--new-only`)
3b. Refresh bulletin_state_stats
4. Health check
5. Trigger dashboard redeploy
+2h: Layer 3 diagnostic agent checks `/health`, sends Telegram summary

### Weekly (Tue 3AM)
`python run_weekly_pipeline.py`
1. Scrape mass times (all 50 states)
2. Bulletin extraction (all states via `extract_bulletins_to_db99.py`)
3. Full SQL rescore
4. Health check + redeploy

### Name Quality Pipeline (per extracted name)
```
1. Extract via 6 regex patterns (staff, honorific, section-header, ministry, intention, prayer)
2. nameparser: parse into first/middle/last/title/suffix
3. probablepeople: detect couple → split if Household type
4. Dictionary score: SSA first + Census last (fast lookup)
5. NER veto: spaCy en_core_web_lg — downgrade if not PERSON entity
6. Consensus: dictionary + NER agreement → high/medium/low
7. SQL blocklist cleanup (runs after sync)
```

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scrape_to_db99.py` | Mass times: CatholicIndex → db99 (stateless) |
| `extract_bulletins_to_db99.py` | Bulletins: discover → download → extract → NER → db99 (stateless) |
| `run_daily_pipeline.py` | Orchestrates daily: scrape + bulletins + rescore + health + redeploy |
| `run_weekly_pipeline.py` | Orchestrates weekly: all states, full rescore |
| `rescore_names_sql.py` | SQL-based rescore + blocklist + stats refresh |
| `backfill_empty_fields.py` | Layer 2: re-parse empty fields from person_name/context |
| `rescore_with_ner.py` | One-time NER rescore of existing names (ran 2026-03-19) |
| `run_bulletin_scraper.py` | Original file-based bulletin pipeline (local use) |
| `run_job.py` | Wrapper that captures stdout/stderr → logs to db99 scrape_log |
| `scripts/prepare_name_reference.py` | Regenerate SSA (with gender) + Census reference data |

## RECENTLY COMPLETED (2026-03-21)

### Couple detection pass (Task 1)
- Column mismatch fixed, `role` column auto-added by `ensure_schema()`
- Triggered one-off job on weekly cron: `python rescore_with_ner.py --couples-only`
- Job ID: `job-d6vj5e7diees73d45cn0` — running on Render

### Downgrade daily cron (Task 2)
- Daily cron downgraded from standard ($25/mo) to starter ($7/mo)
- Dashboard upgraded to standard (2GB) — was crashing on starter (OOM)

### Re-extract states (Task 3)
- Already handled by weekly pipeline — `extract_bulletins_to_db99.py` runs every Tuesday
- Existing PDFs tracked by `text_extracted` boolean, won't be reprocessed
- New PDFs get column detection + Pattern 6 + NER veto automatically

### Optimize rescore (Task 5)
- Added `--new-only` flag to `rescore_names_sql.py` — uses `bulletin_name_id` watermark
- Daily pipeline switched from `--cleanup-only` to `--new-only`
- Full rescore (~11 min on 2.6M rows) only on weekly; daily rescores just new names (seconds)

## RECENTLY COMPLETED (2026-06-13 / 06-14)

### Cloudflare 403 fix — catholicindex.org (commit `1189922`)
- **Root cause:** CatholicIndex.org sits behind Cloudflare, which returns **HTTP 403** to self-identifying bot User-Agents. Our honest `CatholicMassTimesScraper/1.0` UA was hard-403'd, silently breaking the mass-times scrape. Verified: honest UA → 403, Chrome UA → 200 (156 KB) **from the same IP** → a UA block, not an IP block.
- **Fix:** switched `config/settings.py` `USER_AGENT` default to a realistic Chrome UA (used by `src/utils/http.py`). The bulletin paths already used a browser UA.
- **Verified end-to-end on Render's datacenter IP:** one-off probe job succeeded (fetched >50 KB), then a real `run_weekly_pipeline.py --skip-bulletins` job ran ~6 min and **succeeded** through the db99 sync. So the datacenter IP is **not** IP-blocked once the UA is fixed.

### Env-driven proxy support — wired but DORMANT (commit `1189922`)
- Added optional rotating-residential-proxy support across **all** request paths: `src/utils/http.py`, `extract_bulletins_to_db99.py` (PDF downloads), `run_bulletin_scraper.py` (requests session **+ Playwright launch**), `run_resolve_urls.py`. Canonical `PROXY_URL`/`PROXIES` defined in `config/settings.py`.
- **When `PROXY_URL` is unset (the default) everything goes direct** — this is a no-op until enabled on a Render cron. `PROXY_URL` is intentionally **NOT set** on the church crons because we confirmed we don't need it (UA fix alone works from Render).
- To enable later: set `PROXY_URL` to the working 711 endpoint (the case-sensitive `-country-US` URL — see shared-proxy notes). Zero code change required.

### Cron Docker build fix (commit `4ba2b8a`)
- The cron image build was failing at `python -m spacy download en_core_web_lg` with `ModuleNotFoundError: No module named 'click'` — `spacy>=3.7` is unpinned and the resolved typer/spacy stack stopped pulling `click` transitively (last green build had been 2026-05-12). Added explicit `click>=8.1` to `requirements.txt`. Build is green again.

## NEXT TASKS (priority order)

### 0. (watch) Re-pin spacy stack to prevent future build drift
- The `click` fix is surgical; `spacy>=3.7` is still unpinned and could drift again. Consider pinning the spacy/typer/click stack to a known-good set.

### 1. Mass times data normalization
- Events/locations returning junk — some events don't take place at the church
- Need to identify address per event or weed out off-site events
- See plan below

### 2. Monitor couple detection results
- Check job `job-d6vj5e7diees73d45cn0` completion
- Verify split couples appear correctly on dashboard

### 3. Add role column data to dashboard
- `role` column now exists on db99 (added by `ensure_schema`)
- Dashboard table has role column (index 1) but currently shows empty string
- Update `data_loader.py` to SELECT role from view, update view if needed

## DB Local Access
- **Always use** `DB_HOST=10.10.0.8` env var for local connections
- VPC endpoint from Secrets Manager doesn't work locally (only from Render)
- Example: `DB_HOST=10.10.0.8 python tests/test_pipeline_health.py`

## Debug
- `/debug/logs` on dashboard: shows recent scrape_log entries
- `/debug/logs?type=ner_rescore&limit=50`: filter by type
- `run_job.py` wrapper: captures stdout/stderr from any script → logs to db99
