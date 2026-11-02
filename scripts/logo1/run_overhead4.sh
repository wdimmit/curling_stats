#!/bin/bash
# overhead-4 (and its pilot): overhead-3's recipe, the data and the run name as arguments.
# Run on the laptop: run_overhead4.sh /data/wdd/curling/overhead-4/folds/all/data.yaml overhead-4
# nc and the class names come from the data.yaml (0 red, 1 yellow, 2 player).
set -uo pipefail
if [ $# -ne 2 ]; then
  echo "usage: $0 DATA_YAML NAME" >&2
  exit 2
fi
DATA=$1
NAME=$2
if [ ! -f "$DATA" ]; then
  echo "no such data.yaml: $DATA" >&2
  exit 2
fi
cd /data/wdd/curling
P=/data/wdd/curling/$NAME.progress
echo "$(date +%T) start" >> $P
/data/wdd/curling/.venv/bin/yolo detect train model=/data/wdd/curling/yolo11n.pt data=$DATA \
  epochs=100 patience=25 batch=16 imgsz=640 device=0 seed=0 deterministic=True \
  degrees=5.0 fliplr=0.5 flipud=0.0 mosaic=1.0 close_mosaic=8 \
  hsv_h=0.005 hsv_s=0.5 hsv_v=0.4 translate=0.1 scale=0.5 erasing=0.4 \
  project=/data/wdd/curling/runs name=$NAME exist_ok=True > "$(dirname "$DATA")/$NAME.log" 2>&1
echo "$(date +%T) done exit $?" >> $P
