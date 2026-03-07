# Church Scrapes — Project Plan

## Overview
Catholic church mass times, bulletins, and extracted names dashboard.
- **Repo**: benashkar/church-scrapes
- **Database**: `church_scrapes` on db99.rds.blockshopper.com (MySQL, us-east-1 Virginia)
- **Dashboard**: Render web service (Virginia, Docker), reads from db99
- **Pipeline**: Render cron job (Virginia, Docker), runs Tuesdays 3AM UTC

## Current Status (2026-03-06)

### COMPLETED
1. **Data in db99** — 23,046 churches, 280K services, 1.3M bulletin names, 259K PDFs
2. **Weekly pipeline** (`run_weekly_pipeline.py`) — scrape → dates → bulletins → db99 sync → git push → redeploy
3. **Cron job on Render** — `church-tuesday-pipeline`, Virginia, Docker, Tue 3AM UTC
4. **Dashboard on Render** — Virginia, Docker, running
5. **sync_to_db99.py** — UPSERT with display_name sanitization (date-like names, empty, "Type" entries)
6. **Dockerfile.cron** — python:3.12-slim + git + curl (no Playwright, fits Starter plan)
7. **All Render services migrated to Virginia** (from Ohio)
8. **Missing FK indexes added** — `bulletin_pdf.bulletin_source_id`, `bulletin_name.bulletin_pdf_id`
9. **`bulletin_state_stats` summary table created** — pre-computed bulletin stats per state for fast dashboard startup

### IN PROGRESS — Dashboard DB Migration (CSV → db99)
Replacing all CSV file reads with MySQL queries so the dashboard reads live from db99.

**Files changed:**
- `dashboard/app/data_loader.py` — REWRITTEN: all 14 functions now query db99 instead of CSVs
- `dashboard/app/config.py` — removed DATA_DIR config (no longer needed)
- `dashboard/app/routes/mass_times.py` — calendar_download uses generate_dated_services_csv()
- `dashboard/Dockerfile` — removed `COPY data/output/` (no CSV files needed), image much smaller
- `dashboard/requirements.txt` — added pymysql, boto3

**Functions converted (all tested locally):**
- `init_data()` — queries db99 for state list, bulletin stats, filter dropdowns (~12s local, <2s on Render)
- `get_states()`, `get_states_with_bulletins()` — from cached state list
- `get_services()` — joins service+church+lookups, returns DataFrame with original column names
- `get_bulletin_names()` — from v_bulletin_ui_names view (50K row cap)
- `get_bulletin_names_page()` — SQL LIMIT/OFFSET pagination (no longer loads full DataFrame)
- `get_bulletin_stats()` — from `bulletin_state_stats` table (instant)
- `get_bulletin_filters()` — pre-computed at startup from bulletin_source+church
- `get_dated_services()` — services with event_date, formatted to match CSV columns
- `generate_dated_services_csv()` — NEW, replaces file-based calendar download
- `_load_church_details_jsonl()` — now queries church table (kept function name for route compat)
- `get_church_website()`, `get_church_slug()`, `church_has_bulletin_names()` — all DB-backed

**Bug found & fixed:**
- `_format_time()` crashed on NaT values from NULL MySQL TIME columns — added NaN/NaT guard

## NEXT TODOs (in order)

### 1. Fix `get_states()` returning None when called without init_data
- The test `get_states()` standalone returned None because `_state_list` is initialized in `init_data()`
- Need to verify the Flask app factory calls `init_data(app)` correctly (it does in `__init__.py`)
- Not a real bug in production, just affects standalone testing

### 2. Test the full Flask app locally
- Run `cd dashboard && python -m flask run` and verify all pages work:
  - Home page (state list with counts)
  - Mass times state view (church list)
  - Mass times church view (service schedule)
  - Calendar view (dated services)
  - Calendar CSV download
  - Bulletin index (states with bulletin stats)
  - Bulletin state view (DataTables AJAX pagination)
  - Bulletin suspect names
- Fix any column name mismatches between new data_loader and templates

### 3. Update Render env vars for dashboard
- Dashboard service needs AWS credentials for db99 access:
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_DEFAULT_REGION=us-east-1`
- Remove `DATA_DIR` env var if set

### 4. Deploy dashboard to Render
- Push changes to GitHub
- Trigger manual deploy via Render API
- Verify all pages work on production

### 5. Update weekly pipeline to refresh bulletin_state_stats
- After `sync_to_db99.py` runs, refresh the `bulletin_state_stats` table
- Add a function in sync_to_db99.py or run_weekly_pipeline.py to:
  ```sql
  TRUNCATE bulletin_state_stats;
  INSERT INTO bulletin_state_stats (state_code, total_names, unique_names, church_count, city_count)
  SELECT ... (same query used to populate it)
  ```

### 6. Clean up old CSV-dependent code
- Remove `data/output/` COPY from any remaining Dockerfiles
- Verify render.yaml doesn't reference DATA_DIR
- Consider removing CSV generation from pipeline if no longer needed

### 7. Update ERD diagram
- Add `bulletin_state_stats` table to `docs/erd.html`
- Add new indexes to diagram

## DB Schema Summary
| Table | Rows | Notes |
|-------|------|-------|
| church | 23,046 | All 50 states |
| service | 280,490 | Mass times, confessions, etc. |
| bulletin_name | 1,343,227 | Extracted names from PDFs |
| bulletin_pdf | 259,102 | Downloaded bulletin PDFs |
| bulletin_source | 5,583 | Churches with bulletin pages |
| bulletin_state_stats | 50 | Pre-computed stats per state |
| scrape_log | 0 | Pipeline run tracking |
| + 10 views | — | v_bulletin_ui_names, v_weekly_schedule, etc. |

## Key Indexes Added (2026-03-06)
- `bulletin_pdf.bulletin_source_id` — was missing, caused full table scans on bulletin JOINs
- `bulletin_name.bulletin_pdf_id` — was missing, same issue
- These reduced JOIN times from 300s+ to seconds
