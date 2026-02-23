#!/bin/bash
# Auto-commit bulletin scraper results every 30 minutes.
# Watches for new bulletin_names.csv files and commits them.
# Safe to run alongside any bulletin scraper process — never kills or interrupts.
#
# Usage: nohup bash auto_commit_bulletins.sh > auto_commit_bulletins.log 2>&1 &
# Stop:  kill $(cat .auto_commit_bulletins.pid)

cd "$(dirname "$0")" || exit 1
echo $$ > .auto_commit_bulletins.pid

INTERVAL=1800  # 30 minutes
RENDER_SERVICE_ID="srv-d6dqdtkr85hc73c2smng"
RENDER_API_KEY="${RENDER_API_KEY:-$(grep -o 'rnd_[A-Za-z0-9]*' ~/.bashrc 2>/dev/null)}"

echo "=== Bulletin auto-commit watcher ==="
echo "Checking every $((INTERVAL / 60)) minutes for new bulletin data."
echo "PID: $$"
echo ""

while true; do
    sleep $INTERVAL

    # Stage any new/modified bulletin data files
    git add data/output/*/bulletin_names.csv \
            data/output/*/bulletin_names.json \
            data/output/*/bulletin_progress.json \
            data/output/*/bulletin_discovery.json \
            data/output/*/capped_churches.json \
            2>/dev/null

    # Also stage any URL resolver progress
    git add data/output/*/church_details.jsonl \
            data/output/*/resolve_progress.json \
            2>/dev/null

    # Check if there's anything staged
    if git diff --cached --quiet 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') | No new changes to commit."
        continue
    fi

    # Count which states have new bulletin data
    BULLETIN_STATES=$(git diff --cached --name-only | grep 'bulletin_names.csv' | sed 's|data/output/||;s|/bulletin_names.csv||' | tr '\n' ', ' | sed 's/,$//')
    RESOLVER_STATES=$(git diff --cached --name-only | grep 'church_details.jsonl' | sed 's|data/output/||;s|/church_details.jsonl||' | tr '\n' ', ' | sed 's/,$//')

    # Build commit message
    MSG="Auto-commit: bulletin + resolver progress"
    if [ -n "$BULLETIN_STATES" ]; then
        MSG="$MSG\n\nBulletin data updated: $BULLETIN_STATES"
    fi
    if [ -n "$RESOLVER_STATES" ]; then
        MSG="$MSG\nResolver data updated: $RESOLVER_STATES"
    fi

    git commit -m "$(echo -e "$MSG")

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

    # Rebase onto latest remote so deploy always includes newest dashboard code
    git pull --rebase 2>&1
    git push 2>&1

    echo "$(date '+%Y-%m-%d %H:%M:%S') | Committed and pushed. Bulletins: [$BULLETIN_STATES] Resolver: [$RESOLVER_STATES]"

    # Trigger Render deploy
    if [ -n "$RENDER_API_KEY" ]; then
        curl -s -X POST "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys" \
            -H "Authorization: Bearer $RENDER_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{"clearCache": "do_not_clear"}' > /dev/null 2>&1
        echo "$(date '+%Y-%m-%d %H:%M:%S') | Render deploy triggered."
    fi
done
