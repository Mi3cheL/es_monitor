#!/usr/bin/env bash
set -euo pipefail

# Minimalny WM do kiosku
openbox-session >/tmp/openbox.log 2>&1 &

# X ma żyć cały czas
exec sleep infinity
