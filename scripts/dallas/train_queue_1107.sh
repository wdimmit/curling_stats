#!/bin/bash
# DFW training queue, 2026-11-07 (the user): overhead dfw1b -> long dfw2c -> broom bdfw1,
# once the recordings worker has stopped and only the live game shares this GPU.
# Recipes: dfw1a's (overhead-3's), dfw2b's (long-1's), broom4 fine-tuned on broom3's
# augmentation. Each retries at half the batch if the GPU runs out of memory; each
# ends with old vs new on its fold's DFW-only val.
set -uo pipefail
cd /data/wdd/curling
L=/data/wdd/curling/dfw-1107
Y=/data/wdd/curling/.venv/bin/yolo
P=$L/queue.progress
log() { echo "$(date '+%F %T') $*" >> $P; }

until ! docker ps --format '{{.Names}}' | grep -qx dfw-worker2-1; do sleep 60; done
log "recordings worker stopped; queue starts"

train() {  # name batch args...
  local name=$1 batch=$2; shift 2
  for b in $batch $((batch / 2)) $((batch / 4)); do
    [ "$b" -ge 2 ] || break
    log "$name: training, batch $b"
    $Y detect train "$@" batch=$b device=0 seed=0 project=$L/runs name=$name exist_ok=True \
      > $L/$name.log 2>&1
    rc=$?
    if [ $rc -eq 0 ] && [ -f $L/runs/$name/weights/best.pt ]; then log "$name: done"; return 0; fi
    if grep -q "out of memory" $L/$name.log; then log "$name: out of memory at batch $b"; continue; fi
    log "$name: FAILED rc $rc"; return 1
  done
  log "$name: FAILED (memory)"; return 1
}

compare() {  # name data old imgsz
  local name=$1 data=$2 old=$3 sz=$4
  for w in $old $L/runs/$name/weights/best.pt; do
    $Y detect val model=$w data=$data imgsz=$sz batch=4 device=0 project=$L/val name=$name-$(basename $(dirname $(dirname $w)))-$(basename $w .pt) exist_ok=True 2>&1 \
      | grep -E "^\s+(all|red_stone|yellow_stone|broom_head) " | sed "s#^#$(basename $w) #" >> $L/compare-$name.txt
  done
  log "$name: compared ($L/compare-$name.txt)"
}

train dfw1b 16 model=/data/wdd/curling/yolo11n.pt data=$L/folds/dfw1b/data.yaml \
  epochs=100 patience=25 imgsz=640 deterministic=True degrees=5.0 fliplr=0.5 flipud=0.0 mosaic=1.0 \
  close_mosaic=8 hsv_h=0.005 hsv_s=0.5 hsv_v=0.4 translate=0.1 scale=0.5 erasing=0.4 \
  && compare dfw1b $L/folds/dfw1b/data.yaml $L/base/dfw1a.pt 640

train dfw2c 8 model=/data/wdd/curling/yolo11s.pt data=$L/folds/dfw2c/data.yaml \
  imgsz=800 epochs=300 patience=60 mosaic=1.0 close_mosaic=30 scale=0.4 degrees=0.0 fliplr=0.5 \
  flipud=0.0 hsv_h=0.015 hsv_s=0.6 hsv_v=0.4 \
  && compare dfw2c $L/folds/dfw2c/data.yaml $L/base/dfw2b.pt 800

train bdfw1 8 model=$L/base/broom4.pt data=$L/folds/bdfw1/data.yaml \
  imgsz=800 epochs=120 patience=40 mosaic=1.0 close_mosaic=20 scale=0.4 degrees=0.0 fliplr=0.5 \
  flipud=0.0 hsv_h=0.015 hsv_s=0.6 hsv_v=0.4 \
  && compare bdfw1 $L/folds/bdfw1/data.yaml $L/base/broom4.pt 800

log "queue finished"
