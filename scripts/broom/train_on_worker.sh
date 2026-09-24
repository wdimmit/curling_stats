#!/usr/bin/env bash
# Train the broom-head detector on the worker's GPU rather than this laptop's.
#
# Brooms reuse ds13b's recipe unchanged: the same long camera at the same scale,
# small objects on the same ice, and a person clicking boxes with SAM on this
# laptop's card while it trains.
#
# Two reasons, and the second is the one that bites. The worker's RTX 3070
# Laptop is roughly 1.5-2x the A2000 Laptop here at the same 8 GB. And the SAM
# click-to-segment server for the box editor runs HERE, on this card: training
# locally pins it at 98% and every click in the tagger queues behind a training
# step. The 95 ms per click measured in `segserve` was measured on an idle GPU.
#
# The tree is a few hundred small JPEGs, so moving it costs seconds.
#
#   scripts/broom/train_on_worker.sh <local-tree-dir> <run-name> [epochs]
#
# Never --delete on the rsync: deploy/worker.env exists nowhere else.
set -euo pipefail

TREE="${1:?usage: train_on_worker.sh <local-tree-dir> <run-name> [epochs]}"
NAME="${2:?run name}"
EPOCHS="${3:-300}"
HOST=administrator@10.0.0.182
REMOTE=/data/wdd/curling/broom_trees/"$NAME"

echo "==> shipping $TREE to $HOST:$REMOTE"
ssh "$HOST" "mkdir -p $REMOTE"
rsync -a --exclude 'deploy/worker.env' "$TREE"/ "$HOST:$REMOTE"/

# data.yaml carries an absolute path, so it has to be rewritten for the box it
# will actually run on. Doing this remotely keeps the local copy usable too.
ssh "$HOST" "sed -i 's#^path: .*#path: $REMOTE#' $REMOTE/data.yaml && cat $REMOTE/data.yaml"

echo "==> training on the worker GPU"
# `cd; setsid ... &`, not `cd && setsid ... &`: with `&&` the background job is
# the whole list, run in a subshell that holds this ssh channel open until
# training ends (ds13's copy of this script has that wait built in).
ssh "$HOST" "cd /data/wdd/curling; setsid nohup /data/wdd/curling/.venv/bin/python -u -c \"
from ultralytics import YOLO
m = YOLO('/data/wdd/curling/yolo11s.pt')
m.train(data='$REMOTE/data.yaml',
        project='/data/wdd/curling/broom_runs', name='$NAME', exist_ok=True,
        imgsz=800, epochs=$EPOCHS, patience=60, batch=8, seed=0,
        mosaic=1.0, close_mosaic=30, scale=0.4,
        degrees=0.0, fliplr=0.5, flipud=0.0,
        hsv_h=0.015, hsv_s=0.6, hsv_v=0.4, verbose=True)
\" > /tmp/broom_train_$NAME.log 2>&1 < /dev/null & disown"

echo "==> started; follow with:"
echo "    ssh $HOST \"tail -f /tmp/broom_train_$NAME.log\""
echo "==> weights will land at:"
echo "    $HOST:/data/wdd/curling/broom_runs/$NAME/weights/best.pt"
