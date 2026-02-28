#!/bin/bash
set -euo pipefail

log() { echo "[start_kiosk] $(date '+%F %T') $*"; }

export DISPLAY=:0
export HOME=/home/es_admin

URL="http://127.0.0.1:8080/player"

log "BEGIN user=$(whoami) uid=$(id -u) DISPLAY=$DISPLAY"
log "URL=$URL"

# Czekamy aż X server będzie gotowy
for i in $(seq 1 100); do
    if /usr/bin/xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        log "X is ready (try=$i)"
        break
    fi
    sleep 0.2
done

if ! /usr/bin/xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    log "ERROR: X not ready"
    exit 1
fi

# Wyłączenie wygaszacza
if command -v xset >/dev/null 2>&1; then
    xset s off || true
    xset -dpms || true
    xset s noblank || true
fi

# Ukrycie kursora
if command -v unclutter >/dev/null 2>&1; then
    unclutter -idle 0 -root &
fi

log "Starting chromium..."

exec /usr/lib/chromium/chromium \
    --kiosk \
    --start-fullscreen \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI,BackForwardCache \
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --autoplay-policy=no-user-gesture-required \
    --incognito \
    --disk-cache-size=1 \
    "$URL"
