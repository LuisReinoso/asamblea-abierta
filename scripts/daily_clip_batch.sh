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
# Full plenary sessions per night, newest first. Only the current legislative
# period (installed 2025-05-14): older sessions are by members who are not in
# the roster or the voiceprint DB, so they would come out "No identificado".
SESSION_LIMIT=5
SESSION_SINCE=2025-05-14

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
log "=== daily batch starting (clips + sessions) ==="
notify "Asamblea Abierta - inicio" "Batch diario (clips y sesiones) iniciando ($(date '+%Y-%m-%d %H:%M'))"

# Discovery: append new channel videos to data/sessions/index.json, otherwise
# the batch only ever drains the old index (it went stale from 2026-07-10 to
# 2026-10-10 without anyone noticing). Runs before diarization-server so the
# GPU stays with the local LLM meanwhile. Scan depth 700 covers ~3 months of
# clips; limit 80/tab with 2s sleeps keeps the per-video yt-dlp calls well
# under the burst that rate-limited us in July. A failure here is logged and
# notified but doesn't block the batch.
# Only /streams for now: clips are queued by hand for the asambleístas who
# still lack a voiceprint, and new recent clips would sort ahead of them.
log "discovering new videos"
if DISCOVER_OUT=$(timeout 1800 "$REPO_DIR/.venv/bin/python3" scripts/pipeline/01_discover_videos.py \
        --tab full_session --scan-depth 700 --limit 80 --sleep 2 2>&1); then
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

# One pass per video type; each reports on its own, so a night where clips
# are done but sessions fail (or the reverse) is visible in the notification.
SUMMARY=""
ANY_ERROR=0
run_pass() {  # run_pass LABEL run_batch-args...
    local label="$1"; shift
    local out status done_line reasons
    out=$(python3 scripts/run_batch.py "$@" 2>&1)
    status=$?
    echo "$out" >> "$LOG_FILE"
    done_line=$(echo "$out" | grep "BATCH DONE" | tail -1 | sed 's/^.*BATCH DONE — //')
    if echo "$out" | grep -q "YouTube pausado hasta"; then
        log "$label: skipped, YouTube bot-check pause in effect"
        SUMMARY+="$label: en pausa por verificación anti-bot de YouTube. "
    elif echo "$out" | grep -q "Pending: 0 videos"; then
        log "$label: nothing pending"
        SUMMARY+="$label: sin pendientes. "
    elif [ $status -ne 0 ] && [ -z "$done_line" ]; then
        log "$label: run_batch.py crashed with status $status"
        SUMMARY+="$label: falló (exit $status). "
        ANY_ERROR=1
    elif [ $status -ne 0 ]; then
        # Some videos failed. This used to send the same "fin" message as a
        # clean run, which hid 0/20 nights from 2026-08-13 to 2026-10-10.
        reasons=$(echo "$out" | grep -oP '✗ \S+\s+\S+ \[\K[^]]*' | sort | uniq -c | sort -rn | head -3 | tr -s ' ' | tr '\n' ';')
        log "$label: run_batch.py exited with status $status"
        SUMMARY+="$label: $done_line, fallos: ${reasons:-?} "
        ANY_ERROR=1
    else
        log "$label: run_batch.py exited with status 0"
        SUMMARY+="$label: ${done_line:-terminado}. "
    fi
}

run_pass "Clips" --limit "$BATCH_LIMIT" --video-type clip
run_pass "Sesiones" --limit "$SESSION_LIMIT" --video-type full_session --since "$SESSION_SINCE"

ELAPSED=$(( $(date +%s) - START_TIME ))
VOICEPRINT_COUNT=$(python3 -c "import json; print(len(json.load(open('internal/speakers/voiceprints.json'))))" 2>/dev/null || echo "?")
if [ $ANY_ERROR -ne 0 ]; then
    notify "Asamblea Abierta - ERROR" "${SUMMARY}Revisar logs/daily_clip_batch.log"
else
    notify "Asamblea Abierta - fin" "${SUMMARY}Voces en DB: $VOICEPRINT_COUNT. Duración: ${ELAPSED}s"
fi

# Stage only the generated public data. This used to be `git add -A`, which
# would publish anything that landed in the repo outside .gitignore.
git add -- data docs
if ! git diff --cached --quiet; then
    git commit -q -m "data: daily batch $(date +%F)"
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

log "=== daily batch finished (errors: $ANY_ERROR) ==="
