#!/bin/bash
# Train one detector per ds15 fold with ds11a's recipe, verbatim from
# /data/wdd/curling/runs/ds11a/args.yaml -- the only change is the extra
# frames, so any difference in the result is theirs. Runs on the GPU box:
#
#   bash train_folds.sh [fold ...]     # default: the three held-out folds, then all
set -euo pipefail
YOLO=/data/wdd/curling/.venv/bin/yolo
FOLDS=/data/wdd/curling/ds15/folds
folds=("$@"); [ ${#folds[@]} -eq 0 ] && folds=(AEqLTgM25Tc VXU9xwmugRg hOKZoeJNTpM all)
for f in "${folds[@]}"; do
  echo "=== fold $f $(date -Is)"
  $YOLO detect train model=/data/wdd/curling/yolo11n.pt data=$FOLDS/$f/curling.yaml \
    epochs=100 patience=25 batch=16 imgsz=640 device=0 seed=0 deterministic=True \
    degrees=5.0 fliplr=0.5 flipud=0.0 mosaic=1.0 close_mosaic=8 \
    hsv_h=0.005 hsv_s=0.5 hsv_v=0.4 translate=0.1 scale=0.5 erasing=0.4 \
    project=/data/wdd/curling/runs name=ds15-$f exist_ok=True
done
echo "=== all folds done $(date -Is)"
