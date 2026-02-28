#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=:0
export XAUTHORITY=/home/admin/.Xauthority

# start chromium jako admin w tym samym X
exec sudo -u admin -E /opt/es_monitor/app/player/start_kiosk.sh
