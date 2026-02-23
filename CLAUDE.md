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

## File Conventions
- State data: `data/output/{state}/` (lowercase state name, underscores)
- Bulletin CSVs: `data/output/{state}/bulletin_names.csv`
- Church data: `data/output/{state}/church_details.jsonl`
- Progress files: `data/output/{state}/bulletin_progress.json`
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
