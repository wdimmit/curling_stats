#!/usr/bin/env bash
# Train ds13c on the worker once tonight's live streams are over, then score it.
#
# Not before START_AT (22:45), and then only after the worker has been idle for
# IDLE_MIN minutes in a row: the GPU at or below 5% and no lines in the worker
# container's log but its empty claim polls (a POST to /api/worker/claim
# answered 204 every 30 s). Gives up at GIVE_UP_AT rather than share the GPU:
# a job and this training together do not fit in 8 GB.
#
# ds13b's own settings (ds13_runs/wave2/args.yaml), only the data changed:
# ds13c.yaml = wave2 (the band) + hack1 (the hack to +2 m, sides masked).
#
#   scp queue_ds13c.sh eval_hack.py worker:/data/wdd/curling/ds13_runs/ds13c_queue/
#   ssh -f worker 'cd /data/wdd/curling/ds13_runs/ds13c_queue && setsid nohup bash queue_ds13c.sh > queue.log 2>&1 < /dev/null &'
set -u
Q=/data/wdd/curling/ds13_runs/ds13c_queue
PY=/data/wdd/curling/.venv/bin/python
START_AT=${START_AT:-22:45}
GIVE_UP_AT=${GIVE_UP_AT:-tomorrow 04:00}
IDLE_MIN=${IDLE_MIN:-10}
cd "$Q"
say() { echo "$(date -Is) $*" >> status.txt; }

say "queued: start after $START_AT once idle for $IDLE_MIN min; give up at $GIVE_UP_AT"
start=$(date -d "$START_AT" +%s); deadline=$(date -d "$GIVE_UP_AT" +%s); now=$(date +%s)
if [ "$now" -lt "$start" ]; then sleep $((start - now)); fi
say "past $START_AT; waiting for the worker to go quiet"

worker=$(docker ps --filter ancestor=curling-worker:local --format '{{.Names}}' | head -1)
if [ -z "$worker" ]; then say "no running worker container found; not training"; exit 1; fi
quiet=0
while [ "$quiet" -lt "$IDLE_MIN" ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then say "the worker was never idle for $IDLE_MIN min before $GIVE_UP_AT; not training"; exit 1; fi
  util=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1 | tr -d ' ')
  lines=$(docker logs --since 1m "$worker" 2>&1 | grep -v 'api/worker/claim "HTTP/1.1 204' | wc -l)
  if [ "${util:-100}" -le 5 ] && [ "$lines" -eq 0 ]; then quiet=$((quiet + 1)); else quiet=0; fi
  sleep 60
done

say "idle; training ds13c"
t0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"$PY" -u -c "
from ultralytics import YOLO
m = YOLO('/data/wdd/curling/yolo11s.pt')
m.train(data='/data/wdd/curling/ds13_trees/ds13c.yaml',
        project='/data/wdd/curling/ds13_runs', name='ds13c', exist_ok=True,
        imgsz=800, epochs=300, patience=60, batch=8, seed=0,
        mosaic=1.0, close_mosaic=30, scale=0.4,
        degrees=0.0, fliplr=0.5, flipud=0.0,
        hsv_h=0.015, hsv_s=0.6, hsv_v=0.4, verbose=True)
" > train.log 2>&1
rc=$?
docker logs --since "$t0" "$worker" 2>&1 | grep -v 'api/worker/claim "HTTP/1.1 204' > worker_during_training.log
if [ -s worker_during_training.log ]; then
  say "the worker logged $(wc -l < worker_during_training.log) lines during training (the GPU was shared); see worker_during_training.log"
fi
if [ "$rc" -ne 0 ] || [ ! -f /data/wdd/curling/ds13_runs/ds13c/weights/best.pt ]; then
  say "training failed (exit $rc); see train.log"; exit 1
fi
say "trained ($(tail -1 /data/wdd/curling/ds13_runs/ds13c/results.csv | cut -d, -f1) epochs); scoring ds13b and ds13c"
"$PY" -u eval_hack.py ds13b=/data/wdd/curling_score/weights/ds13b.pt \
      ds13c=/data/wdd/curling/ds13_runs/ds13c/weights/best.pt > eval.log 2>&1 \
  && say "scored: $(grep -E '^ds13[bc]:' eval.log | tr '\n' ' ')" \
  || say "scoring failed; see eval.log"
say "done"
