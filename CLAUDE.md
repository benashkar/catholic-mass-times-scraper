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

## What NOT to Do
- Don't dedup names statewide — dedup per church only
- Don't set prayer_list confidence to "medium" — it's "high" (real parishioners)
- Don't use spacy on Python 3.14
- Don't use Unicode special chars in Windows logger output
- Don't forget to update the project plan after changes
