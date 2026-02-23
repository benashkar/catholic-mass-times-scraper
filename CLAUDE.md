# CLAUDE.md - Project Rules & Memory

## Universal Rules
- **After every correction:** Update this CLAUDE.md file so the same mistake isn't repeated.
- **After every change to approach:** Update `mass-times-scraper-project-plan.md` and push to GitHub.
- **Names are per-church unique, not statewide.** Dedup happens at the church level. If "John Smith" appears at 3 churches, he shows up 3 times.
- **Names must be split** into `title`, `first_name`, `middle_name`, `last_name` fields. The full `person_name` string is kept for reference.

## Data Structure Rules
- The goal is **city + name pairs** for downstream matching. Every name row must link back to a church (which has a city and address).
- Preserve **provenance**: every name links to a specific PDF URL and bulletin date, so you can answer "where did this name come from?"
- **Confidence scoring**: prayer_list = HIGH (they are real parishioners), not medium. All named individuals in bulletins are real people.
- False positives will be filtered downstream when matching against a known name list. Prioritize **recall over precision**.

## Technical Gotchas
- **Python 3.14**: `spacy` is incompatible (pydantic error). Use regex-based name extraction instead.
- **Windows cp1252 encoding**: Don't use Unicode symbols like checkmarks in logger output. Use `[OK]`, `[--]`, `[ERR]` instead.
- **`dict.get("key", "")` returns None** when the key exists with a None value. Use `d.get("key") or ""` pattern.
- **CatholicIndex redirect URLs**: Website field contains `/api/out?...` redirects, not actual URLs. Must resolve via `run_resolve_urls.py` first.
- **Background tasks may timeout**: Long-running bulletin scraper runs (hours) should use `nohup` and be monitored. Use `--resume` flag if interrupted.
- **JSONL truncation with --limit**: When using `--limit N`, keep `all_records` separate from `records` for the file rewrite.
- **Docker COPY can't reference parent directories**: `COPY ../data/` fails — Docker build context doesn't include parent dirs. For apps in subdirectories that need repo-root data, use Render's native Python runtime (not Docker) so the full repo is available, or put the Dockerfile at the repo root.
- **`pip` command may not exist**: On this Windows/Git Bash environment, use `python3 -m pip` instead of bare `pip`. Or verify with `which pip` first.

## Bulletin Scraper Rules
- **MAX_PDFS_PER_CHURCH = 100**: First pass grabs up to 100 bulletins per church
- **Capped churches are flagged**: Churches that hit the 100-PDF cap are saved to `capped_churches.json` for a future second pass
- **URL resolver must run first**: States with `/api/out?...` redirect URLs need `run_resolve_urls.py` before the bulletin scraper will find their websites
- **`--resume` flag**: Use when restarting an interrupted run — it skips already-completed churches
- **Run with `nohup`**: Bulletin scraping takes hours per state. Always use `nohup python3 run_bulletin_scraper.py all <state> > log 2>&1 &`
- **3-phase pipeline**: Phases are independent and re-runnable:
  - `discover` — find bulletin page URLs (fast, ~1 req/church)
  - `download` — download PDFs (slow, network-bound)
  - `extract` — parse text + extract names (CPU-bound, no network)
- **Extract phase is safe to re-run**: It reads from downloaded PDFs and overwrites `bulletin_names.csv`. Use `python3 run_bulletin_scraper.py extract <state>` to re-extract with updated logic without re-downloading.
- **`--resume` skips extract if already done**: If you changed extraction logic and need to re-run, use `extract` phase directly (NOT `all --resume`). The `--resume` flag skips churches already in the `extracted` progress dict.

## Name Extraction Architecture
- **ALL name extraction logic lives in ONE file: `run_bulletin_scraper.py`**
- Key functions (keep these updated together):
  - `extract_names_from_text()` — main extraction with 6 pattern groups
  - `parse_name_parts()` — splits "Fr. John M. Smith" into title/first/middle/last
  - `is_valid_name()` — false positive filtering
  - `FALSE_POSITIVE_NAMES` — blocklist set
- **Role vs Title fields** (added Feb 2026):
  - `title` = honorific prefix: Fr., Rev., Dr., Msgr., Dcn., etc.
  - `role` = positional job: Pastor, Business Manager, Chairman, Deacon, Lector, etc.
  - A person can have BOTH: Pastor Fr. Michael Martinez → role="Pastor", title="Fr."
- **6 extraction patterns** (in priority order):
  1. Staff role-name pairs: "Pastor Fr. Michael Martinez", "Business Manager Teresa Mullen"
  2. Honorific-only clergy: "Fr. John Smith" (implies role=Priest)
  3. Section-header roles: "DEACONS" header → all names below get role=Deacon
  4. Ministry contact listings: "Altar Servers, Aeneas Anderson 249-9820"
  5. Mass intentions: "repose of the soul of John Smith"
  6. Prayer lists / sick lists: comma-separated names after prayer headers
  7. Contextual names: capitalized names near ministry keywords (MEDIUM confidence)
- **PDF column bleed**: PDF text extraction merges adjacent columns on the same line (e.g. "Patrick Ledger Saturday Vigil"). The code strips trailing non-name words.
- **Case-insensitive validation**: `is_valid_name()` checks non_name_words case-insensitively
- **Iterative improvement workflow**: Download PDFs once (slow), then re-run `extract` phase as many times as needed with improved logic (fast)

## File Conventions
- State data: `data/output/{state}/` (lowercase state name, underscores)
- Bulletin CSVs: `data/output/{state}/bulletin_names.csv`
- Church data: `data/output/{state}/church_details.jsonl`
- Progress files: `data/output/{state}/bulletin_progress.json`
- Capped churches: `data/output/{state}/capped_churches.json`
- SQL schema: `database/schema.sql`
- Project plan: `mass-times-scraper-project-plan.md` (UPDATE AFTER EVERY CHANGE)

## Commit Conventions
- Always include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` in commits
- Commit message should explain the "why", not just the "what"
- Push to GitHub after commits

## Dashboard (Render)
- Dashboard lives in `dashboard/` subfolder, deployed to Render as standalone web service
- Uses **native Python runtime** (not Docker) — Render clones full repo, so `data/output/` is available via `DATA_DIR=../data/output`
- Flask app factory pattern with 3 blueprints: main, mass_times, bulletin
- Data loaded from CSVs (no Postgres for POC) with LRU cache per state
- render.yaml at repo root configures the service
- Dashboard URL is separate from the medical dashboard — this is its own standalone project

## What NOT to Do
- Don't dedup names statewide — dedup per church only
- Don't set prayer_list confidence to "medium" — it's "high" (real parishioners)
- Don't use spacy on Python 3.14
- Don't use Unicode special chars in Windows logger output
- Don't forget to update the project plan after changes
- Don't use `COPY ../` in Dockerfiles — it can't access parent directories
- Don't assume `pip` exists as a bare command — use `python3 -m pip` or check first
- Don't use `all --resume` to re-run extraction after changing extraction logic — use `extract <state>` directly
- Don't mix up `title` (honorific: Fr.) and `role` (positional: Pastor) — they are separate fields
