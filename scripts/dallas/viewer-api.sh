#!/bin/sh
# Local API showing the DFW game (vvy26k01ZcI): memory backends (state dies with the
# container), yt-dlp metadata, no accounts. http://100.80.66.81:8798/
# MODEL_ID is dfw1a's, the model dfw-worker claims under.
set -e
cd "$(dirname "$0")"
umask 077
[ -s worker_token ] || head -c 32 /dev/urandom | base64 | tr -d '=+/\n' > worker_token
[ -s admin_token ] || head -c 32 /dev/urandom | base64 | tr -d '=+/\n' > admin_token
umask 022
docker rm -f dfw-api >/dev/null 2>&1 || true
docker run -d --name dfw-api --network host \
  -v /data/wdd/scratch/dallas:/w:ro -e PYTHONPATH=/w/code/src \
  -e MEMORY_BACKENDS=1 -e YOUTUBE=ytdlp -e PORT=8798 -e PROXY_HOPS=0 \
  -e PUBLIC_BASE_URL=http://100.80.66.81:8798 \
  -e WORKER_TOKEN="$(cat worker_token)" -e ADMIN_TOKEN="$(cat admin_token)" -e MODEL_ID=dfw1a-584c18d0 \
  -e RATE_HOUR=50 -e RATE_DAY=200 \
  curling-api:ardsley-yt >/dev/null
echo "started dfw-api on :8798"
