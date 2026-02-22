#!/bin/bash
# Auto-commit URL resolver progress every 2 hours
# Run this alongside run_resolve_urls.py to save progress periodically
#
# Usage: bash auto_commit_progress.sh
# Stop: Ctrl+C or kill the process

INTERVAL=7200  # 2 hours in seconds
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Auto-commit started. Committing every $((INTERVAL / 3600)) hours."
echo "Project dir: $PROJECT_DIR"
echo "Press Ctrl+C to stop."

while true; do
    sleep $INTERVAL

    cd "$PROJECT_DIR" || exit 1

    # Check if there are any changes to commit
    if git diff --quiet data/output/ 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') | No changes to commit"
        continue
    fi

    # Count how many states have been updated
    CHANGED=$(git diff --name-only data/output/ | grep 'church_details.jsonl' | wc -l)

    # Stage and commit
    git add data/output/*/church_details.jsonl data/output/*/resolve_progress.json 2>/dev/null
    git commit -m "URL resolver progress: $CHANGED states updated

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

    git push 2>&1

    echo "$(date '+%Y-%m-%d %H:%M:%S') | Committed and pushed ($CHANGED states updated)"
done
