# CLAUDE.md - Universal Rules (All Projects)

## Purpose
This file documents mistakes and gotchas so they are NEVER repeated — across any project.

## Technical Gotchas (Never Do Again)
- **Python 3.14**: `spacy` is incompatible (pydantic error). Use alternatives.
- **Windows cp1252 encoding**: Don't use Unicode symbols like checkmarks in logger output. Use `[OK]`, `[--]`, `[ERR]` instead.
- **`dict.get("key", "")` returns None** when the key exists with a None value. Use `d.get("key") or ""` pattern instead.
- **Docker COPY can't reference parent directories**: `COPY ../data/` fails — Docker build context doesn't include parent dirs. Either put Dockerfile at repo root, or use a non-Docker deployment (e.g. Render native Python runtime).
- **`pip` command may not exist**: On Windows/Git Bash, use `python3 -m pip` instead of bare `pip`. Or verify with `which pip` first.
- **Background tasks may timeout**: Long-running scripts (hours) should use `nohup` and be monitored.
- **JSONL truncation with --limit**: When using `--limit N` on a script that rewrites a file, keep `all_records` separate from `records` for the file rewrite.
- **Case-insensitive validation**: When checking words against a blocklist, always `.lower()` both sides. A set containing `'are'` won't match `'Are'`.
- **`--resume` flag can silently skip re-processing**: If you changed processing logic and need to re-run, don't use `--resume` — it skips already-completed items. Run the specific phase directly.

## Commit Conventions
- Always include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` in commits
- Commit message should explain the "why", not just the "what"
- Push to GitHub after commits

## What NOT to Do (Universal)
- Don't use `COPY ../` in Dockerfiles
- Don't assume `pip` exists as a bare command
- Don't use spacy on Python 3.14
- Don't use Unicode special chars in Windows logger output
- Don't forget to update the project plan after changes
- Don't use `--resume` to re-run after changing processing logic
