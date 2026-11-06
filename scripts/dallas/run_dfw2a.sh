#!/bin/bash
# dfw2a: long-1's fold plus the user's dfw2 long-camera frames (DFW green and
# yellow stones, the local /label server). long-1's recipe, unchanged.
set -uo pipefail
cd /data/wdd/curling
P=/data/wdd/curling/dfw2/dfw2a.progress
echo "$(date +%T) start" >> $P
/data/wdd/curling/.venv/bin/yolo detect train model=/data/wdd/curling/yolo11s.pt \
  data=/data/wdd/curling/dfw2/folds/all/data.yaml \
  imgsz=800 epochs=300 patience=60 batch=8 device=0 seed=0 \
  mosaic=1.0 close_mosaic=30 scale=0.4 degrees=0.0 fliplr=0.5 flipud=0.0 \
  hsv_h=0.015 hsv_s=0.6 hsv_v=0.4 \
  project=/data/wdd/curling/runs name=dfw2a exist_ok=True > /data/wdd/curling/dfw2/dfw2a.log 2>&1
echo "$(date +%T) done exit $?" >> $P
