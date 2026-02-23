#!/bin/bash
# Batch bulletin scraper for all ready states (URLs resolved, no bulletin progress)
# Runs all 3 phases (discover + download + extract) for each state sequentially.
# After each state: commits data, pushes to GitHub, triggers Render deploy.
#
# Usage: nohup bash run_bulletin_batch.sh > bulletin_batch.log 2>&1 &

cd "$(dirname "$0")"

# Render deploy trigger (requires RENDER_API_KEY in env)
RENDER_SERVICE_ID="srv-d6dqdtkr85hc73c2smng"
RENDER_API_KEY="${RENDER_API_KEY:-$(grep -o 'rnd_[A-Za-z0-9]*' ~/.bashrc 2>/dev/null)}"

deploy_to_render() {
    if [ -n "$RENDER_API_KEY" ]; then
        echo "  Triggering Render deploy..."
        curl -s -X POST "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys" \
            -H "Authorization: Bearer $RENDER_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{"clearCache": "do_not_clear"}' > /dev/null 2>&1
        echo "  Render deploy triggered."
    else
        echo "  WARNING: No RENDER_API_KEY found. Skipping deploy."
    fi
}

commit_and_push() {
    local state="$1"
    local state_dir="data/output/$state"

    # Check if there are any new files to commit
    if [ ! -f "$state_dir/bulletin_names.csv" ]; then
        echo "  No bulletin_names.csv found for $state, skipping commit."
        return
    fi

    # Stage bulletin data files
    git add "$state_dir/bulletin_names.csv" \
            "$state_dir/bulletin_names.json" \
            "$state_dir/bulletin_progress.json" \
            "$state_dir/bulletin_discovery.json" \
            "$state_dir/capped_churches.json" \
            2>/dev/null

    # Check if there's anything staged
    if git diff --cached --quiet 2>/dev/null; then
        echo "  No new changes to commit for $state."
        return
    fi

    # Count names for commit message
    local name_count=$(wc -l < "$state_dir/bulletin_names.csv" 2>/dev/null || echo "0")
    name_count=$((name_count - 1))  # subtract header row

    git commit -m "Add bulletin names for $state ($name_count names extracted)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

    git push 2>&1
    echo "  Committed and pushed $state bulletin data ($name_count names)."
}

STATES=(
    california
    michigan
    minnesota
    massachusetts
    florida
    louisiana
    indiana
    iowa
    kansas
    connecticut
    maryland
    kentucky
    colorado
    alabama
    maine
    arkansas
    mississippi
    alaska
    idaho
    hawaii
    delaware
)

echo "=== BULLETIN BATCH SCRAPER ==="
echo "Starting at $(date)"
echo "Processing ${#STATES[@]} states"
echo ""

for state in "${STATES[@]}"; do
    echo "================================================================"
    echo "[$state] Starting at $(date)"
    echo "================================================================"

    python3 -u run_bulletin_scraper.py all "$state" --resume

    echo "[$state] Scraping finished at $(date)"

    # Auto-commit, push, and deploy after each state
    commit_and_push "$state"
    deploy_to_render

    echo "[$state] Fully complete at $(date)"
    echo ""
done

echo "=== ALL DONE at $(date) ==="
