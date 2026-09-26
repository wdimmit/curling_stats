#!/bin/bash
# Four-player parity on the GPU worker.
#   scripts/doubles/fours_parity.sh run LABEL SHA [--no-longview]
#   scripts/doubles/fours_parity.sh compare LABEL_A LABEL_B
set -euo pipefail
HOST=administrator@10.0.0.182
REMOTE=/data/wdd/curling/doubles-parity
VIDS="VXU9xwmugRg hOKZoeJNTpM AEqLTgM25Tc"
case $1 in
run)
  LABEL=$2 SHA=$3 EXTRA=${4:-}
  ssh $HOST "mkdir -p $REMOTE/code-$SHA $REMOTE/out"
  git archive $SHA src | ssh $HOST "tar -x -C $REMOTE/code-$SHA"
  for vid in $VIDS; do
    ssh $HOST "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml run --rm --no-deps \
      -v $REMOTE/code-$SHA:/code:ro -v $REMOTE/out:/parity -e PYTHONPATH=/code/src worker \
      sh -c 'curling-score analyze https://www.youtube.com/watch?v=$vid --out /parity/$LABEL/$vid $EXTRA > /parity/$LABEL-$vid.log 2>&1'"
    echo "$LABEL $vid done"
  done ;;
compare)
  A=$2 B=$3
  for vid in $VIDS; do
    echo "== $vid"
    ssh $HOST "python3 - $REMOTE/out/$A/$vid/timeline.json $REMOTE/out/$B/$vid/timeline.json" \
      < scripts/doubles/compare_docs.py || true
  done ;;
esac
