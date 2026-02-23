#!/bin/bash
# Auto-pipeline: For each state, resolve URLs then scrape bulletins.
# Runs 3 states in parallel to finish faster (~1 day vs ~2 days).
# Each state hits different church websites so no Cloudflare conflicts.
#
# Usage: nohup bash auto_pipeline.sh > auto_pipeline.log 2>&1 &

cd "$(dirname "$0")"

MAX_PARALLEL=3

STATES=(
    pennsylvania
    new_york
    nebraska
    connecticut
    new_mexico
    iowa
    louisiana
    new_jersey
    indiana
    wisconsin
    oklahoma
    rhode_island
    west_virginia
    missouri
    wyoming
    south_dakota
    vermont
    arkansas
    colorado
    north_dakota
    alaska
    hawaii
    south_carolina
    maryland
    montana
    washington
    alabama
    kansas
    north_carolina
    virginia
    idaho
    tennessee
    mississippi
    nevada
    oregon
    utah
    delaware
    kentucky
    maine
    new_hampshire
    georgia
    minnesota
    michigan
    massachusetts
)

# Process a single state: resolve URLs -> scrape bulletins -> commit
process_state() {
    local state="$1"
    local state_dir="data/output/$state"
    local logfile="pipeline_${state}.log"

    exec > "$logfile" 2>&1

    if [ ! -f "$state_dir/church_details.jsonl" ]; then
        echo "[$state] No church data found, skipping."
        return
    fi

    echo "================================================================"
    echo "[$state] Starting at $(date)"
    echo "================================================================"

    # Step 1: Resolve URLs (with retry loop)
    echo "[$state] Step 1: Resolving URLs..."
    python3 -u run_resolve_urls.py "$state" --retry-loop
    echo "[$state] URL resolution complete at $(date)"

    # Step 2: Run bulletin scraper (discover + download + extract)
    echo "[$state] Step 2: Running bulletin scraper..."
    python3 -u run_bulletin_scraper.py all "$state"
    echo "[$state] Bulletin scraper complete at $(date)"

    echo "[$state] Fully complete at $(date)"
}

# Git commit for a completed state (run from main process to avoid conflicts)
commit_state() {
    local state="$1"
    local state_dir="data/output/$state"

    if [ -f "$state_dir/bulletin_names.csv" ]; then
        git add "$state_dir/bulletin_names.csv" \
                "$state_dir/bulletin_names.json" \
                "$state_dir/bulletin_progress.json" \
                "$state_dir/bulletin_discovery.json" \
                "$state_dir/capped_churches.json" \
                "$state_dir/church_details.jsonl" \
                "$state_dir/url_resolver_progress.json" \
                2>/dev/null

        if ! git diff --cached --quiet 2>/dev/null; then
            name_count=$(wc -l < "$state_dir/bulletin_names.csv" 2>/dev/null || echo "0")
            name_count=$((name_count - 1))
            git commit -m "Add bulletin + URL data for $state ($name_count names extracted)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
            # Rebase onto latest remote so deploy always includes newest dashboard code
            git pull --rebase 2>&1
            git push 2>&1
            echo "[$state] Committed and pushed."
        else
            echo "[$state] No new changes to commit."
        fi
    fi
}

echo "=== AUTO PIPELINE (${MAX_PARALLEL} parallel) ==="
echo "Started at $(date)"
echo "Processing ${#STATES[@]} states, ${MAX_PARALLEL} at a time"
echo ""

# Track running background jobs as (pid, state) pairs
declare -a PIDS=()
declare -a PID_STATES=()
idx=0

while [ $idx -lt ${#STATES[@]} ] || [ ${#PIDS[@]} -gt 0 ]; do

    # Launch new jobs if we have capacity and states remaining
    while [ ${#PIDS[@]} -lt $MAX_PARALLEL ] && [ $idx -lt ${#STATES[@]} ]; do
        state="${STATES[$idx]}"
        echo "[$(date)] Launching: $state"
        process_state "$state" &
        PIDS+=($!)
        PID_STATES+=("$state")
        idx=$((idx + 1))
        sleep 2  # stagger launches slightly
    done

    # Wait for any one job to finish
    if [ ${#PIDS[@]} -gt 0 ]; then
        # Poll until at least one PID finishes
        while true; do
            for i in "${!PIDS[@]}"; do
                pid="${PIDS[$i]}"
                if ! kill -0 "$pid" 2>/dev/null; then
                    # This job finished
                    wait "$pid"
                    state="${PID_STATES[$i]}"
                    echo "[$(date)] Completed: $state"

                    # Commit from main process (avoids git conflicts)
                    commit_state "$state"

                    # Remove from arrays
                    unset 'PIDS[i]'
                    unset 'PID_STATES[i]'
                    PIDS=("${PIDS[@]}")
                    PID_STATES=("${PID_STATES[@]}")
                    break 2
                fi
            done
            sleep 30
        done
    fi
done

echo ""
echo "=== ALL DONE at $(date) ==="
