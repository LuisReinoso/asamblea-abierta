#!/bin/bash
set -uo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
LOG_FILE="$REPO_DIR/logs/daily_clip_batch.log"
BATCH_LIMIT=8

mkdir -p "$REPO_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

log "=== daily clip batch starting ==="

source "$REPO_DIR/.venv/bin/activate"

OUTPUT=$(python3 scripts/run_batch.py --limit "$BATCH_LIMIT" --video-type clip 2>&1)
STATUS=$?
echo "$OUTPUT" >> "$LOG_FILE"

if echo "$OUTPUT" | grep -q "Pending: 0 videos"; then
    log "no pending clips left, nothing to do"
    exit 0
fi

if [ $STATUS -ne 0 ]; then
    log "run_batch.py exited with status $STATUS"
fi

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -q -m "data: daily clip batch $(date +%F)"
    log "committed changes"
else
    log "no changes to commit"
fi

log "=== daily clip batch finished (exit $STATUS) ==="
