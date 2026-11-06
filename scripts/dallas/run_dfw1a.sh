#!/bin/bash
# dfw1a: overhead-3's fold plus dfw1 (DFW green and yellow stones). overhead-3's recipe.
set -uo pipefail
cd /data/wdd/curling
P=/data/wdd/curling/dfw1/dfw1a.progress
echo "$(date +%T) start" >> $P
/data/wdd/curling/.venv/bin/yolo detect train model=/data/wdd/curling/yolo11n.pt data=/data/wdd/curling/dfw1/folds/all/data.yaml \
  epochs=100 patience=25 batch=16 imgsz=640 device=0 seed=0 deterministic=True \
  degrees=5.0 fliplr=0.5 flipud=0.0 mosaic=1.0 close_mosaic=8 \
  hsv_h=0.005 hsv_s=0.5 hsv_v=0.4 translate=0.1 scale=0.5 erasing=0.4 \
  project=/data/wdd/curling/runs name=dfw1a exist_ok=True > /data/wdd/curling/dfw1/dfw1a.log 2>&1
echo "$(date +%T) done exit $?" >> $P
