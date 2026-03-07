#!/bin/bash
# Batch bulletin scraper — always keeps 2 states running.
# When one finishes, immediately starts the next. No idle time between pairs.
# After each state finishes: commits data, pushes to GitHub, triggers Render deploy.
#
# Usage: nohup bash run_bulletin_batch.sh > bulletin_batch.log 2>&1 &

cd "$(dirname "$0")"
mkdir -p logs

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

commit_and_push_state() {
    local state="$1"
    local state_dir="data/output/$state"

    if [ ! -f "$state_dir/bulletin_names.csv" ]; then
        echo "  [$state] No bulletin_names.csv found, skipping commit."
        return
    fi

    git add "$state_dir/bulletin_names.csv" \
            "$state_dir/bulletin_names.json" \
            "$state_dir/bulletin_progress.json" \
            "$state_dir/bulletin_discovery.json" \
            "$state_dir/capped_churches.json" \
            2>/dev/null

    if git diff --cached --quiet 2>/dev/null; then
        echo "  [$state] No new changes to commit."
        return
    fi

    local name_count=$(wc -l < "$state_dir/bulletin_names.csv" 2>/dev/null || echo "0")
    name_count=$((name_count - 1))

    git commit -m "Add bulletin names for $state ($name_count names extracted)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

    git pull --rebase 2>&1
    git push 2>&1
    deploy_to_render
    echo "  [$state] Committed + pushed ($name_count names)."
}

on_state_done() {
    local state="$1"
    local exit_code="$2"
    echo "[$state] Finished (exit code $exit_code) at $(date)"

    CSV="data/output/$state/bulletin_names.csv"
    if [ -f "$CSV" ]; then
        LINES=$(($(wc -l < "$CSV") - 1))
        echo "  [$state] $LINES names extracted"
    else
        echo "  [$state] no bulletin_names.csv produced"
    fi

    commit_and_push_state "$state"
}

start_state() {
    local state="$1"
    echo "[$state] Starting at $(date)"
    python3 -u run_bulletin_scraper.py all "$state" --resume \
        > "logs/bulletin_${state}.log" 2>&1 &
    echo $!
}

# ── States to process ────────────────────────────────────────────────────────
STATES=(
    idaho kansas kentucky maine maryland mississippi missouri montana
    nevada new_hampshire north_carolina north_dakota oklahoma oregon
    rhode_island south_carolina south_dakota tennessee utah vermont
    virginia washington west_virginia wyoming
)

MAX_PARALLEL=2

echo "=== BULLETIN BATCH SCRAPER (always $MAX_PARALLEL running) ==="
echo "Starting at $(date)"
echo "States to process: ${#STATES[@]}"
echo ""

# Track running PIDs and their state names
declare -A PID_TO_STATE
NEXT_IDX=0

# Start initial batch
while [ ${#PID_TO_STATE[@]} -lt $MAX_PARALLEL ] && [ $NEXT_IDX -lt ${#STATES[@]} ]; do
    STATE="${STATES[$NEXT_IDX]}"
    PID=$(start_state "$STATE")
    PID_TO_STATE[$PID]="$STATE"
    NEXT_IDX=$((NEXT_IDX + 1))
done

# Main loop: wait for any to finish, then start next
while [ ${#PID_TO_STATE[@]} -gt 0 ]; do
    # Wait for any child process to exit
    wait -n -p DONE_PID 2>/dev/null
    EXIT_CODE=$?

    # If wait -n -p isn't supported (older bash), fall back
    if [ -z "$DONE_PID" ]; then
        # Fallback: poll which PIDs are still alive
        for PID in "${!PID_TO_STATE[@]}"; do
            if ! kill -0 "$PID" 2>/dev/null; then
                wait "$PID" 2>/dev/null
                EXIT_CODE=$?
                DONE_PID=$PID
                break
            fi
        done
        # If all still running, sleep briefly and retry
        if [ -z "$DONE_PID" ]; then
            sleep 5
            continue
        fi
    fi

    # Handle completed state
    DONE_STATE="${PID_TO_STATE[$DONE_PID]}"
    unset "PID_TO_STATE[$DONE_PID]"
    on_state_done "$DONE_STATE" "$EXIT_CODE"

    # Start next state if available
    if [ $NEXT_IDX -lt ${#STATES[@]} ]; then
        STATE="${STATES[$NEXT_IDX]}"
        PID=$(start_state "$STATE")
        PID_TO_STATE[$PID]="$STATE"
        NEXT_IDX=$((NEXT_IDX + 1))
    fi

    DONE_PID=""
done

echo ""
echo "=== ALL ${#STATES[@]} STATES DONE at $(date) ==="
