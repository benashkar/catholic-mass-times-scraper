#!/bin/bash
# Run bulletin scraper for one state, then commit+push.
# Usage: bash run_parallel_bulletins.sh <state>
cd "$(dirname "$0")"
state="$1"
logfile="bulletin_${state}.log"

echo "[$state] Starting at $(date)" > "$logfile"
python3 -u run_bulletin_scraper.py all "$state" --resume >> "$logfile" 2>&1
echo "[$state] Scraping finished at $(date)" >> "$logfile"

# Commit + push (use a lock file to avoid concurrent git conflicts)
(
    flock -x 200
    state_dir="data/output/$state"
    if [ -f "$state_dir/bulletin_names.csv" ]; then
        git add "$state_dir/bulletin_names.csv" \
                "$state_dir/bulletin_names.json" \
                "$state_dir/bulletin_progress.json" \
                "$state_dir/bulletin_discovery.json" \
                "$state_dir/capped_churches.json" \
                2>/dev/null
        if ! git diff --cached --quiet 2>/dev/null; then
            name_count=$(($(wc -l < "$state_dir/bulletin_names.csv") - 1))
            git commit -m "Add bulletin names for $state ($name_count names extracted)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
            # Rebase onto latest remote so deploy always includes newest dashboard code
            git pull --rebase 2>&1
            git push 2>&1
            echo "[$state] Committed and pushed ($name_count names)." >> "$logfile"
        fi
    fi
) 200>/tmp/git_push.lock

echo "[$state] FULLY DONE at $(date)" >> "$logfile"
