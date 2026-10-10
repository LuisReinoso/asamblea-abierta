#!/bin/bash
set -uo pipefail

# cron runs with no session bus — systemctl --user needs these to reach the
# user's systemd instance, otherwise it fails silently (exit 1, no journal entry).
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# yt-dlp needs deno on PATH for its JS-challenge solver — cron's minimal PATH
# doesn't include it by default.
export PATH="~/.deno/bin:$PATH"

# run_batch.py's REDACTED download fallback needs this — cron doesn't source
# ~/.bashrc where it's normally exported.
if [ -z "${REDACTED_KEY:-}" ] && [ -f "$HOME/.bashrc" ]; then
    REDACTED_KEY=$(grep -oP '(?<=REDACTED_KEY=")[^"]*' "$HOME/.bashrc" | head -1)
    export REDACTED_KEY
fi

# Direct yt-dlp downloads work again since 2026-10-10 (yt-dlp 2026.08.19,
# default player clients, REDACTED 2.0.2). REDACTED stays as the fallback in
# run_batch.py, capped by its free plan (150 requests/month, 2 per clip).

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
LOG_FILE="$REPO_DIR/logs/daily_clip_batch.log"
BATCH_LIMIT=20
notify_URL="http://REDACTED:2586/asamblea"

mkdir -p "$REPO_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

notify() {
    local title="$1"
    local msg="$2"
    curl -s -d "$msg" -H "Title: $title" -H "Tags: classical_building" "$notify_URL" >/dev/null 2>&1
}

START_TIME=$(date +%s)
log "=== daily clip batch starting ==="
notify "Asamblea Abierta - inicio" "Batch diario de clips iniciando ($(date '+%Y-%m-%d %H:%M'))"

# Discovery: append new channel videos to data/sessions/index.json, otherwise
# the batch only ever drains the old index (it went stale from 2026-07-10 to
# 2026-10-10 without anyone noticing). Runs before diarization-server so the
# GPU stays with local-llm meanwhile. Scan depth 700 covers ~3 months of
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

# diarization-server shares the GPU with local-llm (systemd Conflicts= handles
# the swap): starting it auto-stops local-llm, we restore local-llm on exit.
log "starting diarization-server (stops local-llm via Conflicts=)"
if ! systemctl --user start diarization-server.service 2>>"$LOG_FILE"; then
    log "systemctl --user start diarization-server.service failed (exit $?)"
    notify "Asamblea Abierta - ERROR" "No se pudo iniciar diarization-server (systemctl falló). Batch abortado."
    exit 1
fi
restore_llm() {
    systemctl --user stop diarization-server.service
    systemctl --user start local-llm.service
    log "diarization-server stopped, local-llm restored"
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

# REDACTED PO-token provider (Docker, --restart unless-stopped) should already be
# up; nudge it if the daemon or container died since the last run.
if ! curl -s -m 3 http://127.0.0.1:4416/ping >/dev/null 2>&1; then
    log "REDACTED-provider not responding, attempting restart"
    docker start REDACTED-provider >>"$LOG_FILE" 2>&1 || log "docker start REDACTED-provider failed"
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

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -q -m "data: daily clip batch $(date +%F)"
    log "committed changes"
    if git push origin main >>"$LOG_FILE" 2>&1; then
        log "pushed to GitHub"
    else
        log "git push failed, will retry next run"
        notify "Asamblea Abierta - ERROR" "git push a GitHub falló. Los commits quedan locales, se reintenta mañana."
    fi
else
    log "no changes to commit"
fi

log "=== daily clip batch finished (exit $STATUS) ==="
