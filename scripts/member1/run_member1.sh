#!/bin/bash
# overhead-1 and long-1: the shipped models' sets (ds17b, ds13e with hack1r) plus
# Will's own member boxes of 2026-10-27, each with its own recipe unchanged.
# Folds: heldout (3 videos of member frames held out) then all. Then eval.
set -uo pipefail
cd /data/wdd/curling
Y=/data/wdd/curling/.venv/bin/yolo
P=/data/wdd/curling/member1.progress
log() { echo "$(date +%T) $*" >> $P; }
ovh() {  # fold name
  log "start $2"
  $Y detect train model=/data/wdd/curling/yolo11n.pt data=/data/wdd/curling/overhead-1/folds/$1/data.yaml \
    epochs=100 patience=25 batch=16 imgsz=640 device=0 seed=0 deterministic=True \
    degrees=5.0 fliplr=0.5 flipud=0.0 mosaic=1.0 close_mosaic=8 \
    hsv_h=0.005 hsv_s=0.5 hsv_v=0.4 translate=0.1 scale=0.5 erasing=0.4 \
    project=/data/wdd/curling/runs name=$2 exist_ok=True > /data/wdd/curling/overhead-1/$2.log 2>&1
  log "done $2 exit $?"
}
lng() {
  log "start $2"
  $Y detect train model=/data/wdd/curling/yolo11s.pt data=/data/wdd/curling/long-1/folds/$1/data.yaml \
    imgsz=800 epochs=300 patience=60 batch=8 device=0 seed=0 \
    mosaic=1.0 close_mosaic=30 scale=0.4 degrees=0.0 fliplr=0.5 flipud=0.0 \
    hsv_h=0.015 hsv_s=0.6 hsv_v=0.4 \
    project=/data/wdd/curling/runs name=$2 exist_ok=True > /data/wdd/curling/long-1/$2.log 2>&1
  log "done $2 exit $?"
}
ovh heldout overhead-1-heldout
lng heldout long-1-heldout
ovh all overhead-1
lng all long-1
log "eval"
/data/wdd/curling/.venv/bin/python /data/wdd/curling/scripts/eval_member1.py > /data/wdd/curling/member1_eval.log 2>&1
log "all done exit $?"
