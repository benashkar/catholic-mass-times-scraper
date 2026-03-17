# Church Scrapes — Project Plan

## Overview
Catholic church mass times, bulletins, and extracted names dashboard.
- **Repo**: benashkar/catholic-mass-times-scraper
- **Database**: `church_scrapes` on db99 (MySQL, us-east-1 Virginia, private IP `10.10.0.8`)
- **Dashboard**: Render web service (Virginia, Docker), reads from db99 via AWS Secrets Manager
- **Pipeline**: Two Render cron jobs — daily mass times + weekly bulletins

## Current Status (2026-03-16)

### COMPLETED
1. **Data in db99** — 23,046 churches, 280K services, 1.3M bulletin names, 259K PDFs
2. **Dashboard reads from db99** — All 14 query functions converted from CSV to MySQL (commit `0443093`)
3. **Confidence filtering** — Dashboard only shows medium+high confidence names; suspect review page shows all
4. **bulletin_state_stats** — Pre-computed stats count only medium+high confidence, non-suspect names
5. **Daily cron job** (`church-daily-scrape`) — Mon-Sun excl Tue, 3 AM UTC, `--skip-bulletins`
6. **Weekly cron job** (`church-weekly-bulletins`) — Tuesdays 3 AM UTC, full pipeline with bulletin extraction
7. **Old cron suspended** — `church-tuesday-pipeline` suspended, replaced by daily+weekly
8. **No Render PostgreSQL** — Decommissioned, all data on db99
9. **Pushed to GitHub** — All commits pushed including CSV→db99 conversion, confidence filtering, cron split
10. **Dashboard deployed on Render** — Virginia, Docker, live (but no data until PrivateLink fixed)
11. **.gitignore updated** — Analysis scripts, backup files, screenshots, Claude config all ignored
12. **Pipeline service ID fixed** — `run_weekly_pipeline.py` redeploy step uses correct dashboard ID
13. **Secrets Manager timeout** — Increased from 5s to 30s for cold starts, added error logging
14. **DB connection** — Reads host from env var (priority), then Secrets Manager, then fallback

### BLOCKER: db99 PrivateLink
After RDS upgrade, the VPC endpoint service (`vpce-svc-00a7fca302afe04af`) no longer routes to db99.
Ops team needs to update the NLB target group to new IP `10.10.0.8:3306`.
Reference: pipeline-core PrivateLink (`vpce-svc-0fffcbe7aac42ba3e`) works correctly.

**Workaround in place:** `DB_HOST=10.10.0.8` set as Render env var on all 3 services.
Will work once PrivateLink is fixed and private IP is routable from Render.

### AFTER PRIVATELINK FIX
- [ ] Verify `/health` shows `states_loaded > 0`
- [ ] Test all dashboard routes (home, mass times, bulletin, suspect)
- [ ] Confirm bulletin names DataTable shows NO low-confidence entries
- [ ] Confirm suspect page still shows low-confidence names
- [ ] Trigger test run of daily cron (`--states ohio`)
- [ ] Trigger test run of weekly cron
- [ ] Delete suspended old cron `crn-d6liockr85hc73a8a110`
- [ ] Update AWS secret DB_HOST once PrivateLink works, remove DB_HOST env var from Render

## Render Services

| Service | ID | Type | Schedule |
|---------|-----|------|----------|
| catholic-church-dashboard | `srv-d6li8dtm5p6s73chuh7g` | web | — |
| church-daily-scrape | `crn-d6s8st3uibrs73e7b740` | cron | `0 3 * * 0,1,3,4,5,6` |
| church-weekly-bulletins | `crn-d6s8t02a214c73bt62s0` | cron | `0 3 * * 2` |
| church-tuesday-pipeline | `crn-d6liockr85hc73a8a110` | cron | **SUSPENDED** |

## Pipeline Flow

### Daily (Mon-Sun excl Tue)
`python run_weekly_pipeline.py --skip-bulletins`
1. Scrape mass times (all 50 states from CatholicIndex.org)
2. Regenerate 12-week dated services CSVs
3. Sync to db99 (UPSERT churches + services)
4. Git commit + push
5. Trigger dashboard redeploy

### Weekly (Tuesdays)
`python run_weekly_pipeline.py`
1. Scrape mass times (all 50 states)
2. Regenerate 12-week dated services
3. **Bulletin pipeline**: discover → download → extract text → parse names → confidence score
4. Sync to db99 (UPSERT churches + services + bulletin names)
5. Git commit + push
6. Trigger dashboard redeploy

### Deduplication
- Churches: UPSERT by `slug` (unique key)
- Services: UPSERT by `(church_id, source_service_id)`
- Bulletin sources: `INSERT IGNORE` by `church_id`
- Bulletin PDFs: `INSERT IGNORE` by `(bulletin_source_id, pdf_url)`
- Bulletin names: `INSERT IGNORE` by `(bulletin_pdf_id, person_name)`

## DB Schema Summary
| Table | Rows | Notes |
|-------|------|-------|
| church | 23,046 | All 50 states |
| service | 280,490 | Mass times, confessions, etc. |
| bulletin_name | 1,343,227 | Extracted names from PDFs |
| bulletin_pdf | 259,102 | Downloaded bulletin PDFs |
| bulletin_source | 5,583 | Churches with bulletin pages |
| bulletin_state_stats | 50 | Pre-computed stats (medium+high confidence only) |
| scrape_log | — | Pipeline run tracking |
| + 10 views | — | v_bulletin_ui_names, v_weekly_schedule, etc. |

## Key Files
| File | Purpose |
|------|---------|
| `dashboard/app/data_loader.py` | All DB query functions with confidence filtering |
| `dashboard/app/routes/bulletin.py` | Bulletin routes, suspect page uses `include_low=True` |
| `render.yaml` | Render service definitions (daily + weekly cron) |
| `run_weekly_pipeline.py` | Pipeline orchestrator with `--skip-bulletins` flag |
| `sync_to_db99.py` | UPSERT to db99, refreshes bulletin_state_stats |
| `src/utils/db_connection.py` | Shared DB connection (env var > secret > fallback) |
