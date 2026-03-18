# Church Scrapes — Project Plan

## Overview
Catholic church mass times, bulletins, and extracted names dashboard.
- **Repo**: benashkar/catholic-mass-times-scraper
- **Database**: `church_scrapes` on db99 (MySQL, us-east-1 Virginia)
- **Dashboard**: Render web service (Virginia, Docker), reads from db99 via AWS Secrets Manager
- **Pipeline**: Two Render cron jobs — daily mass times + weekly bulletins
- **Name Engine**: `benashkar/names_people_matcher` (`C:\Users\cashk\OneDrive\names_people_matcher`)

## Current Status (2026-03-18)

### COMPLETED
1. **Dashboard live** — 50 states, medium+high confidence names, shareable page URLs
2. **SQL rescore** — ref_ssa_names + ref_census_surnames on db99, junk blocklist, lowercase cleanup
3. **Junk cleanup** — ~62K records removed (Alzheimer, Fish Fry, lowercase, day names, religious terms, etc.)
4. **Column detection** — Integrated into `run_bulletin_scraper.py` via `src/utils/pdf_columns.py`
5. **Pattern 6 fix** — Changed from 200-char proximity to line-based matching (keyword line + next 5 lines)
6. **Direct-to-db99 scraper** — `scrape_to_db99.py` reads church list from db99, scrapes CatholicIndex, UPSERTs directly. No local files needed.
7. **Daily pipeline** — `run_daily_pipeline.py` orchestrates: scrape → rescore → health check → redeploy
8. **Health checks** — 7 automated checks (table counts, confidence distribution, junk rate, lowercase, etc.)
9. **PrivateLink fixed** — VPC endpoint works from Render
10. **max_connect_errors** — Set to 1M by ops team (prevents host blocking)
11. **Old cron deleted** — `church-tuesday-pipeline` removed

### VERIFIED ON RENDER
- `rescore_names_sql.py` — succeeded (11 min)
- `scrape_to_db99.py` — succeeded locally (3 Ohio churches in 9s, wrote to db99)
- `run_daily_pipeline.py --state OH --limit 10` — **testing now on Render**
- Weekly cron standard plan — upgraded for 1-hour runtime

### CURRENT DATA
- 2.6M bulletin_name rows total, ~1.2M medium+high after cleanup
- 23,046 churches, 280K services
- ref_ssa_names (100K), ref_census_surnames (162K) on db99

## Render Services

| Service | ID | Type | Plan | Command |
|---------|-----|------|------|---------|
| catholic-church-dashboard | `srv-d6li8dtm5p6s73chuh7g` | web | starter | gunicorn |
| church-daily-scrape | `crn-d6s8st3uibrs73e7b740` | cron (daily excl Tue) | starter | `python run_daily_pipeline.py` |
| church-weekly-bulletins | `crn-d6s8t02a214c73bt62s0` | cron (Tue 3AM) | **standard** | `python run_weekly_pipeline.py` |

## Pipeline Architecture

### Daily (stateless, scrapes directly to db99)
`python run_daily_pipeline.py`
1. Read church list from db99
2. Scrape each church from CatholicIndex.org
3. UPSERT church + services directly to db99 (no local files)
4. Rescore names via SQL
5. Health check
6. Trigger dashboard redeploy

### Weekly (needs refactor — currently uses local files)
`python run_weekly_pipeline.py`
1. Scrape mass times (all 50 states)
2. Regenerate dated services
3. Bulletin pipeline: discover → download → extract → parse names
4. Sync local files to db99
5. Rescore names via SQL
6. Health check
7. Git push + redeploy

**TODO:** Weekly pipeline also needs direct-to-db99 refactor for bulletin names.

## NEXT TASKS (priority order)

### 1. Optimize rescore to only score new/changed names
Current rescore hits all 2.6M names every run — wasteful.
Fix: Only rescore names where `updated_at > last_rescore_at` or add a `rescored_at` column.
Alternative: Run rescore only on the states that were scraped in this run.

### 2. Re-extract Georgia bulletins with new code
- Column detection + Pattern 6 line-based fix are in place
- Run: `python run_bulletin_scraper.py all georgia` (will re-extract from existing PDFs)
- Compare 3-word name count before/after
- Note: Our Lady of Lourdes (GA) PDFs are image-based — needs OCR, not column detection

### 3. Husband+wife name splitting
- Signal: word 1 is strongly male + word 2 is strongly female + word 3 is Census surname
- SSA data already has M/F gender at line 330 of prepare_name_reference.py (currently discarded)
- Implementation plan documented in previous version of this file

### 4. Mass times data cleanup
- Events/locations returning junk in the dashboard
- Need to review and clean up service display_names
- Add blocklist for service-level junk (similar to bulletin name blocklist)

### 5. Weekly pipeline direct-to-db99 refactor
- Bulletin names need same treatment as mass times
- Extract names from PDF → UPSERT directly to db99
- Eliminates need for local files and persistent storage

### 6. Pattern 6 further improvements
- Current: line-based (keyword line + next 5 lines)
- Testing showed column merging is mostly from Pattern 6, not PDF layout
- Consider: require name to be the ONLY content on its line (list format)
- Consider: reduce from 6 lines to 3 lines proximity

## DB Local Access
- **Always use** `DB_HOST=10.10.0.8` env var for local connections
- VPC endpoint from Secrets Manager doesn't work locally (only from Render)
- Example: `DB_HOST=10.10.0.8 python tests/test_pipeline_health.py`

## Useful Queries
See bottom of previous PROJECT_PLAN.md version (queries for unique people by state, by city, by church)
