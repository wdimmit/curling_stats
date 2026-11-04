#!/bin/bash
# overhead-2: overhead-1's recipe on ds17b's set plus all of Will's overhead
# member boxes of 2026-10-30 (the red batch red-2026-10-29 + the FP harvest).
# Folds: heldout (3 videos + every fifth red-batch video held out) then all. Then eval.
set -uo pipefail
cd /data/wdd/curling
Y=/data/wdd/curling/.venv/bin/yolo
P=/data/wdd/curling/overhead2.progress
log() { echo "$(date +%T) $*" >> $P; }
ovh() {  # fold name
  log "start $2"
  $Y detect train model=/data/wdd/curling/yolo11n.pt data=/data/wdd/curling/overhead-2/folds/$1/data.yaml \
    epochs=100 patience=25 batch=16 imgsz=640 device=0 seed=0 deterministic=True \
    degrees=5.0 fliplr=0.5 flipud=0.0 mosaic=1.0 close_mosaic=8 \
    hsv_h=0.005 hsv_s=0.5 hsv_v=0.4 translate=0.1 scale=0.5 erasing=0.4 \
    project=/data/wdd/curling/runs name=$2 exist_ok=True > /data/wdd/curling/overhead-2/$2.log 2>&1
  log "done $2 exit $?"
}
ovh heldout overhead-2-heldout
ovh all overhead-2
log "eval"
/data/wdd/curling/.venv/bin/python /data/wdd/curling/scripts/eval_overhead2.py > /data/wdd/curling/overhead2_eval.log 2>&1
log "all done exit $?"
