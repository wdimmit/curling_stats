#!/bin/bash
# The 16-game shot-list replay (ds15's harness) for one code tree and one
# overhead model, in 4 streams:
#   parity16.sh <code-label> <out-label> <weights>
#   (code in $B/code-<code-label>, output in $B/out-parity/<out-label>)
B=/data/wdd/curling/ds15/games
c=$1; o=$2; w=$3
out=$B/out-parity/$o; mkdir -p $out
export CURLING_SCORE_CACHE=$B/root PYTHONPATH=$B/code-$c/src
stream() {
  for tl in "$@"; do
    name=$(basename $tl .json)
    vid=$(python3 -c "import json;print(json.load(open('$tl'))['source']['video_id'])")
    timeout 5400 /data/wdd/curling/.venv/bin/python $B/code-$c/scripts/phase3/split_report.py $tl \
      --cache-root $B/root --video $B/root/videos/$vid.mp4 --before $B/empty.json \
      --weights $w --out $out/$name.json > $out/$name.log 2>&1
    echo "$name exit $? $(date +%H:%M)" >> $out/progress.txt
  done
}
mapfile -t TLS < <(ls $B/timelines/*.json)
for s in 0 1 2 3; do
  part=(); for ((i=s; i<${#TLS[@]}; i+=4)); do part+=("${TLS[i]}"); done
  stream "${part[@]}" &
done
wait
echo ALL DONE >> $out/progress.txt
