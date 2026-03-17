# Church Scrapes — Project Plan

## Overview
Catholic church mass times, bulletins, and extracted names dashboard.
- **Repo**: benashkar/catholic-mass-times-scraper
- **Database**: `church_scrapes` on db99 (MySQL, us-east-1 Virginia, private IP `10.10.0.8`)
- **Dashboard**: Render web service (Virginia, Docker), reads from db99 via AWS Secrets Manager
- **Pipeline**: Two Render cron jobs — daily mass times + weekly bulletins
- **Name Engine**: `benashkar/names_people_matcher` (`C:\Users\cashk\OneDrive\names_people_matcher`)

## Current Status (2026-03-17)

### COMPLETED
1. **Data in db99** — 23,046 churches, 280K services, 1.3M bulletin names, 259K PDFs
2. **Dashboard reads from db99** — All 14 query functions converted from CSV to MySQL
3. **Confidence filtering** — Dashboard shows medium+high confidence, non-suspect names
4. **SQL-based re-scoring** — All 1.3M names re-scored in 22 seconds using `ref_ssa_names` + `ref_census_surnames` lookup tables on db99
5. **Daily cron** (`church-daily-scrape`) — Mon-Sun excl Tue, 3 AM UTC, `--skip-bulletins`
6. **Weekly cron** (`church-weekly-bulletins`) — Tuesdays 3 AM UTC, full pipeline
7. **Old cron suspended** — `church-tuesday-pipeline` replaced
8. **PrivateLink fixed** — Ops team updated VPC endpoint after RDS upgrade
9. **DB_HOST env vars removed** — Services use VPC endpoint from Secrets Manager
10. **Reference tables on db99** — `ref_ssa_names` (100K), `ref_census_surnames` (162K)
11. **Name engine package** — `benashkar/names_people_matcher` scaffolded with 3 engines + column detection

### CURRENT DATA (medium+high, non-suspect)
- **821,800 unique people** across 50 states (1,229,453 total records)
- Top states: CA 96K, IL 81K, NY 69K, TX 44K, OH 41K

### VERIFY (after PrivateLink fix)
- [ ] `/health` shows `states_loaded > 0` and `db: connected`
- [ ] Dashboard routes work (home, mass times, bulletin, suspect)
- [ ] Trigger test run of daily cron
- [ ] Delete suspended old cron `crn-d6liockr85hc73a8a110`

## NEXT: Column Detection (Fix Merged Names)

### Problem
pdfplumber reads multi-column PDFs linearly, merging names across columns:
- "Jaiden Harris Aba" = "Jaiden Harris" (col 1) + "Aba" (col 2)
- Our Lady of Lourdes Atlanta alone has 645+ merged 3-word names
- These score "high" because all words are real SSA/Census names individually

### Solution: Column-Aware PDF Extraction
Code exists in `names_people_matcher/name_engine/pdf_columns.py`. Tested on Georgia bulletins — correctly detects 2-3 column layouts.

### Implementation Steps
1. **Integrate column detection into `run_bulletin_scraper.py`**
   - Replace `page.extract_text()` calls with `extract_text_by_columns()`
   - Extract names per-column instead of per-page
   - File: `run_bulletin_scraper.py`, function `extract_text_from_pdf()` (~line 1200)

2. **Re-extract Georgia as test case**
   - Run bulletin pipeline on Georgia only with column detection
   - Compare name counts before/after
   - Verify merged names eliminated

3. **Re-score with SQL after re-extraction**
   - Run SQL UPDATE on db99 using ref tables (22 seconds)
   - Refresh `bulletin_state_stats`

4. **Roll out to all 50 states**
   - Run weekly pipeline with column detection enabled
   - Monitor for regressions

5. **Add spaCy NER engine (requires Python 3.12 Docker)**
   - Third scoring engine validates names in context
   - Catches "Silver Angels Meet", "Everything Athens" etc.
   - Can't run on local Python 3.14, but works in Docker

### Key Files
| File | Purpose |
|------|---------|
| `names_people_matcher/name_engine/pdf_columns.py` | Column detection code (ready) |
| `run_bulletin_scraper.py` | Bulletin pipeline (~4100 lines, needs column integration) |
| `run_bulletin_scraper.py:extract_text_from_pdf()` | Where to plug in column detection |
| `run_bulletin_scraper.py:extract_names_from_text()` | 6 regex pattern groups for name extraction |
| `names_people_matcher/rescore_db99.py` | SQL-based rescoring script |

## NEXT: Husband+Wife Couple Name Detection

### Problem
3-word names like "John Mary Smith" are ambiguous — could be one person (first-middle-last)
or a married couple (John Smith + Mary Smith). Church bulletins frequently list couples this way
in mass intentions, prayer lists, and parishioner directories.

### Signal: Gender Contrast
If word 1 is **strongly male** AND word 2 is **strongly female** (or vice versa) AND word 3 is
a Census surname → it's a couple. Same-gender words → first-middle-last.

### Algorithm
```
1. Get male_ratio for word1 and word2 from SSA data (0.0 = female, 1.0 = male)
2. Check word3 is in Census surnames
3. If word1 male_ratio > 0.90 AND word2 male_ratio < 0.10 → COUPLE
4. If word1 male_ratio < 0.10 AND word2 male_ratio > 0.90 → COUPLE
5. Otherwise → first-middle-last (not a couple)
```

### Examples
- "John Mary Smith" → male(0.99) + female(0.01) → **COUPLE** → "John Smith" + "Mary Smith"
- "John Robert Smith" → male(0.99) + male(0.99) → **NOT couple** (first-middle-last)
- "Mary Ann Johnson" → female(0.01) + female(0.02) → **NOT couple** (middle name)

### Implementation Steps
1. **Modify `scripts/prepare_name_reference.py`** (line 330)
   - Stop discarding the sex column from SSA data
   - Compute `male_ratio = male_count / (male_count + female_count)` per name
   - Output new `ssa_first_names_gendered.csv` with columns: name, rank, male_ratio

2. **Add `detect_couple_name()` to `names_people_matcher`**
   - Input: 3-word name string
   - Uses gendered SSA data + Census surnames
   - Returns original name or tuple of two split names
   - Conservative threshold (0.90) to avoid false splits

3. **Integrate into extraction pipeline**
   - Post-processing step after `clean_extracted_name()`
   - When couple detected, produce TWO bulletin_name records
   - Add `name_type` field: "individual" vs "couple_split"

4. **Also fix "&" / "and" separator patterns**
   - `run_bulletin_scraper.py` line 1597 splits on `[,&\n]+` in section headers
   - Extend to mass intentions: "requested by John & Mary Smith" → two records

### No new dependencies needed
- SSA raw data already has M/F gender (currently discarded at line 330)
- `names-dataset` (already installed) as fallback for international names

## NEXT: Junk Name Blocklist (SQL-based)

### Completed Cleanups (2026-03-17)
Removed ~62K junk records from db99 using SQL blocklist:
- All-lowercase names (9,260)
- Lowercase first names (22,728)
- Day/month abbreviations as first names: Wed, Tue, Mon, Jan, etc.
- Common English words: or, and, the, for, but, all, his, her, etc.
- Religious terms: Alzheimer, Cancer, Hospice, Fish Fry, Sacrament, etc.
- Place names as last names: City, County, Church, Academy, Avenue, etc.
- Religious figures: Christ Jesus, Virgin Mary, Infant Jesus, Mother Teresa

### Remaining Issues
- 3-word merged column names still present (needs column detection fix)
- Some church/saint names slipping through
- Pattern 6 (ministry_contextual) still too loose — produces most false positives

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
3. **Bulletin pipeline**: discover → download → extract text (column-aware) → parse names → confidence score
4. Sync to db99 (UPSERT churches + services + bulletin names)
5. SQL rescore using ref tables
6. Git commit + push
7. Trigger dashboard redeploy

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
| bulletin_state_stats | 50 | Pre-computed stats (medium+high, non-suspect) |
| ref_ssa_names | 100,364 | SSA baby names reference |
| ref_census_surnames | 162,254 | Census 2010 surnames reference |
| scrape_log | — | Pipeline run tracking |
| + 10 views | — | v_bulletin_ui_names, v_weekly_schedule, etc. |

## Useful Queries

### Unique people by state (medium+high)
```sql
SELECT c.state_code,
       COUNT(DISTINCT CONCAT(bn.first_name, ' ', bn.last_name)) AS unique_people
FROM bulletin_name bn
JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
JOIN church c ON bs.church_id = c.church_id
WHERE bn.confidence IN ('high', 'medium') AND bn.is_suspect = 0
  AND bn.first_name != '' AND bn.last_name != ''
GROUP BY c.state_code ORDER BY unique_people DESC;
```

### People details for a state
```sql
-- Change 'GA' to any state. Change IN ('high') to IN ('high','medium') for medium too.
SELECT DISTINCT bn.first_name, bn.middle_name, bn.last_name,
       c.name AS church_name, c.city AS church_city, c.state_code
FROM bulletin_name bn
JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
JOIN church c ON bs.church_id = c.church_id
WHERE c.state_code = 'GA'
  AND bn.confidence IN ('high')  -- Add 'medium' for medium matches
  AND bn.is_suspect = 0
  AND bn.first_name != '' AND bn.last_name != ''
ORDER BY bn.last_name, bn.first_name;
```

### Unique people by city by state
```sql
SELECT c.state_code, c.city,
       COUNT(DISTINCT CONCAT(bn.first_name, ' ', bn.last_name)) AS unique_people
FROM bulletin_name bn
JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
JOIN church c ON bs.church_id = c.church_id
WHERE bn.confidence IN ('high', 'medium') AND bn.is_suspect = 0
  AND bn.first_name != '' AND bn.last_name != ''
GROUP BY c.state_code, c.city
ORDER BY c.state_code, unique_people DESC;
```
