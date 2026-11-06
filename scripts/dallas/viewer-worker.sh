#!/bin/sh
# Worker for the DFW game: ardsley-club image + the cincinnati code in ../code, CLUB=dallas
# with dfw1a (overheads) and dfw2a (long cameras), and the cache that holds the
# NORMALISED video (scripts/dallas/normalize.py) under the video's id -- the
# worker cannot normalise the raw stream itself yet. API_URL = the API's PUBLIC_BASE_URL.
set -e
cd "$(dirname "$0")"
docker rm -f dfw-worker >/dev/null 2>&1 || true
docker run -d --name dfw-worker --gpus all --network host --shm-size 4g \
  -v /data/wdd/scratch/dallas:/w -e PYTHONPATH=/w/code/src \
  -e API_URL=http://100.80.66.81:8798 -e WORKER_TOKEN="$(cat worker_token)" -e WORKER_ID=dfw-laptop \
  -e CURLING_SCORE_CACHE=/w/cache-true -e WORKER_OUT=/w/wout -e WORKER_CACHE_GB=60 -e WORKER_MIN_FREE_GB=40 \
  -e WEIGHTS=/w/weights/dfw1a.pt -e CURLING_SCORE_SIDE_WEIGHTS=/w/weights/dfw2a.pt -e CLUB=dallas \
  curling-worker:ardsley-club >/dev/null
echo "started dfw-worker"
