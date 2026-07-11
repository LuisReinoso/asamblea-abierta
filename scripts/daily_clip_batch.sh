#!/bin/bash
set -uo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
LOG_FILE="$REPO_DIR/logs/daily_clip_batch.log"
BATCH_LIMIT=8
NTFY_URL="http://100.110.12.76:2586/asamblea"

mkdir -p "$REPO_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

notify() {
    local title="$1"
    local msg="$2"
    curl -s -d "$msg" -H "Title: $title" -H "Tags: classical_building" "$NTFY_URL" >/dev/null 2>&1
}

START_TIME=$(date +%s)
log "=== daily clip batch starting ==="
notify "Asamblea Abierta - inicio" "Batch diario de clips iniciando ($(date '+%Y-%m-%d %H:%M'))"

source "$REPO_DIR/.venv/bin/activate"

OUTPUT=$(python3 scripts/run_batch.py --limit "$BATCH_LIMIT" --video-type clip 2>&1)
STATUS=$?
echo "$OUTPUT" >> "$LOG_FILE"

ELAPSED=$(( $(date +%s) - START_TIME ))

if echo "$OUTPUT" | grep -q "Pending: 0 videos"; then
    log "no pending clips left, nothing to do"
    notify "Asamblea Abierta - sin pendientes" "No quedan clips pendientes. Base de voces completa."
    exit 0
fi

DONE_LINE=$(echo "$OUTPUT" | grep "BATCH DONE" | tail -1)
VOICEPRINT_COUNT=$(python3 -c "import json; print(len(json.load(open('internal/speakers/voiceprints.json'))))" 2>/dev/null || echo "?")

if [ $STATUS -ne 0 ] && [ -z "$DONE_LINE" ]; then
    log "run_batch.py crashed with status $STATUS"
    notify "Asamblea Abierta - ERROR" "El batch diario falló (exit $STATUS) sin completar. Revisar logs/daily_clip_batch.log"
else
    log "run_batch.py exited with status $STATUS"
    notify "Asamblea Abierta - fin" "${DONE_LINE:-Batch terminado}. Voces en DB: $VOICEPRINT_COUNT. Duración: ${ELAPSED}s"
fi

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -q -m "data: daily clip batch $(date +%F)"
    log "committed changes"
else
    log "no changes to commit"
fi

log "=== daily clip batch finished (exit $STATUS) ==="
