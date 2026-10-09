#!/usr/bin/env bash
# One stone-detector run on the 3080 with ds11a/ds16a recipe, in the worker image.
#   [EPOCHS=n PATIENCE=n] ./train.sh <base.pt> <run-name> [fold]     e.g. EPOCHS=300 PATIENCE=50 ./train.sh yolo26s.pt ds17b-26s-e300
# A base given by bare name (yolo26s.pt) is fetched into weights/ by Ultralytics.
set -euo pipefail
BASE=${1:?base}; NAME=${2:?run}; FOLD=${3:-all}
cd /data/curling/stone/weights
docker run --rm --gpus all --shm-size 8g --entrypoint "" --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e YOLO_CONFIG_DIR=/tmp -v /data/curling:/data/curling -w /data/curling/stone/weights \
  --name "train-$NAME" curling-worker:local python -u -c "
from ultralytics import YOLO
m = YOLO(\"$BASE\")
m.train(data=\"/data/curling/stone/folds/$FOLD/curling.yaml\", project=\"/data/curling/stone/runs\", name=\"$NAME\",
        exist_ok=True, epochs=${EPOCHS:-100}, patience=${PATIENCE:-25}, batch=16, imgsz=640, device=0, seed=0, deterministic=True,
        degrees=5.0, fliplr=0.5, flipud=0.0, mosaic=1.0, close_mosaic=8,
        hsv_h=0.005, hsv_s=0.5, hsv_v=0.4, translate=0.1, scale=0.5, erasing=0.4)
" > /data/curling/stone/$NAME.log 2>&1
echo "$(date +%T) $NAME exit $? epochs $(tail -1 /data/curling/stone/runs/$NAME/results.csv | cut -d, -f1)" >> /data/curling/stone/progress.txt
