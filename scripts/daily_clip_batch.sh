#!/bin/bash
set -uo pipefail

# cron runs with no session bus — systemctl --user needs these to reach the
# user's systemd instance, otherwise it fails silently (exit 1, no journal entry).
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
LOG_FILE="$REPO_DIR/logs/daily_clip_batch.log"
BATCH_LIMIT=20

mkdir -p "$REPO_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Notifications are a no-op unless the local config defines notify().
notify() { :; }

# Machine-local config (gitignored): PATH additions, API keys, model paths,
# notify(), before_batch() and GPU_RESTORE_SERVICE.
if [ -f "$REPO_DIR/internal/local.sh" ]; then
    source "$REPO_DIR/internal/local.sh"
fi

START_TIME=$(date +%s)
log "=== daily clip batch starting ==="
notify "Asamblea Abierta - inicio" "Batch diario de clips iniciando ($(date '+%Y-%m-%d %H:%M'))"

# Discovery: append new channel videos to data/sessions/index.json, otherwise
# the batch only ever drains the old index (it went stale from 2026-07-10 to
# 2026-10-10 without anyone noticing). Runs before diarization-server so the
# GPU stays with the local LLM meanwhile. Scan depth 700 covers ~3 months of
# clips; limit 80/tab with 2s sleeps keeps the per-video yt-dlp calls well
# under the burst that rate-limited us in July. A failure here is logged and
# notified but doesn't block the batch.
log "discovering new videos"
if DISCOVER_OUT=$(timeout 1800 "$REPO_DIR/.venv/bin/python3" scripts/pipeline/01_discover_videos.py \
        --scan-depth 700 --limit 80 --sleep 2 2>&1); then
    log "discovery: $(echo "$DISCOVER_OUT" | grep -E 'Added/updated|Nothing new|No new sessions' | tail -1)"
else
    log "discovery failed (exit $?): $(echo "$DISCOVER_OUT" | tail -3 | tr '\n' ' ')"
    notify "Asamblea Abierta - aviso" "Discovery falló; el batch sigue con el índice actual. Revisar logs/daily_clip_batch.log"
fi

# diarization-server shares the GPU with another service (systemd Conflicts=
# handles the swap): starting it stops that service, we restore it on exit.
log "starting diarization-server"
if ! systemctl --user start diarization-server.service 2>>"$LOG_FILE"; then
    log "systemctl --user start diarization-server.service failed (exit $?)"
    notify "Asamblea Abierta - ERROR" "No se pudo iniciar diarization-server (systemctl falló). Batch abortado."
    exit 1
fi
restore_llm() {
    systemctl --user stop diarization-server.service
    if [ -n "${GPU_RESTORE_SERVICE:-}" ]; then
        systemctl --user start "$GPU_RESTORE_SERVICE"
    fi
    log "diarization-server stopped, GPU service restored"
}
trap restore_llm EXIT

DIARIZE_READY=0
for i in $(seq 1 20); do
    if curl -s -m 3 http://localhost:8001/ >/dev/null 2>&1; then
        DIARIZE_READY=1
        break
    fi
    sleep 3
done

if [ "$DIARIZE_READY" -ne 1 ]; then
    log "diarization-server failed to come up after 60s, aborting batch"
    notify "Asamblea Abierta - ERROR" "diarization-server no levantó a tiempo. Batch abortado."
    exit 1
fi
log "diarization-server ready"

if type before_batch >/dev/null 2>&1; then
    before_batch
fi

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
elif [ $STATUS -ne 0 ]; then
    # Some clips failed. This used to send the same "fin" message as a clean
    # run, which hid 0/20 nights from 2026-08-13 to 2026-10-10.
    log "run_batch.py exited with status $STATUS"
    FAIL_REASONS=$(echo "$OUTPUT" | grep -oP '✗ \S+\s+\S+ \[\K[^]]*' | sort | uniq -c | sort -rn | head -3 | tr -s ' ' | tr '\n' ';')
    notify "Asamblea Abierta - ERROR" "${DONE_LINE}. Fallos: ${FAIL_REASONS:-?} Revisar logs/daily_clip_batch.log"
else
    log "run_batch.py exited with status $STATUS"
    notify "Asamblea Abierta - fin" "${DONE_LINE:-Batch terminado}. Voces en DB: $VOICEPRINT_COUNT. Duración: ${ELAPSED}s"
fi

# Stage only the generated public data. This used to be `git add -A`, which
# would publish anything that landed in the repo outside .gitignore.
git add -- data docs
if ! git diff --cached --quiet; then
    git commit -q -m "data: daily clip batch $(date +%F)"
    log "committed changes"
else
    log "no data changes to commit"
fi

OTHER_CHANGES=$(git status --porcelain -- . ':!data' ':!docs')
if [ -n "$OTHER_CHANGES" ]; then
    log "left uncommitted (outside data/ and docs/): $(echo "$OTHER_CHANGES" | tr '\n' ' ')"
fi

# Push whenever local is ahead of origin, not only after a new commit, so a
# failed push really is retried on the next run.
git fetch -q origin main >>"$LOG_FILE" 2>&1
if [ -n "$(git rev-list origin/main..HEAD)" ]; then
    if git push origin main >>"$LOG_FILE" 2>&1; then
        log "pushed to GitHub"
    else
        log "git push failed, will retry next run"
        notify "Asamblea Abierta - ERROR" "git push a GitHub falló. Los commits quedan locales, se reintenta mañana."
    fi
fi

log "=== daily clip batch finished (exit $STATUS) ==="
