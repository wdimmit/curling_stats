#!/bin/sh
# Capture this worktree's viewer across the state matrix and diff it against a
# saved snapshot.  usage: scripts/domcheck.sh BASELINE.json OUT.json
set -e
# SERVE lets the snapshot come from a different checkout than the tools --
# which is how the pre-port baseline is captured with the current script.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SERVE=${SERVE:-$ROOT}
TL=${TIMELINE:?set TIMELINE to a timeline.json}
VENV=${VENV:-/home/tcuser/src/curling_score/.venv/bin/python}
tmp=$(mktemp -d)
timeout 400 "$VENV" -u "$SERVE/scripts/devserve.py" "$TL" > "$tmp/ds.out" 2>"$tmp/ds.err" &
DS=$!
i=0; while [ $i -lt 120 ]; do grep -q catalogue "$tmp/ds.out" 2>/dev/null && break; sleep 0.5; i=$((i+1)); done
E=$(grep 'edit '      "$tmp/ds.out" | awk '{print $2}')
V=$(grep 'view-only'  "$tmp/ds.out" | awk '{print $2}')
G=$(grep 'review '    "$tmp/ds.out" | awk '{print $2}')
/usr/bin/google-chrome --headless=new --disable-gpu --no-sandbox \
  --remote-debugging-port=9222 --user-data-dir="$tmp/chrome" about:blank >/dev/null 2>&1 &
CH=$!
sleep 4
node "$ROOT/scripts/domsnap.mjs" "$E" "$V" "$G" --out "$2" > "$tmp/snap.log" 2>&1 || tail -5 "$tmp/snap.log"
kill $CH $DS 2>/dev/null || true
wait 2>/dev/null || true
node "$ROOT/scripts/domsnap.mjs" --diff "$1" "$2"
