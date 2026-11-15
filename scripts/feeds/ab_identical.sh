#!/usr/bin/env bash
# The feeds phase 1 gate: the same VOD analysed by the code before the change
# (BASE_SRC) and by this branch (HEAD_SRC) must give a byte-identical
# timeline.json, and the head must not add a single detection-cache entry
# (every key it computes is one the base already wrote).
#
#   BASE_SRC=/path/to/base/src HEAD_SRC=/path/to/head/src \
#   WORK=/scratch/feeds-ab COLD_IDS="Qzh8XQrxqww" \
#     scripts/feeds/ab_identical.sh hOKZoeJNTpM Qzh8XQrxqww ...
#
# Phases: every id on the base (cold, filling one shared detection cache), a
# snapshot of that cache, every id on the head (warm), a snapshot, then the
# comparison and the set of cache entries the head added (must be none). The
# base and head phases may run JOBS ids at once.
#
# Parameters (environment):
#   BASE_SRC, HEAD_SRC  the two `src` trees; each side imports only its own
#   PYTHON              interpreter with the dependencies (default: python3)
#   RUNNER              optional command prefix the interpreter runs under, for
#                       a container: e.g. `docker run --rm -i --gpus device=@GPU@
#                       -v ... -e PYTHONPATH -e CURLING_SCORE_CACHE
#                       -e PYTHONHASHSEED --entrypoint env image`. The command is
#                       `$RUNNER nice -n $NICE $PYTHON ...`, so the entrypoint
#                       must be `env`. `@GPU@` becomes the job's entry of GPUS.
#                       The trees, WORK and VIDEOS must be visible to it at the
#                       same paths.
#   JOBS, GPUS          ids run at once per phase (default 1); GPUS is a list of
#                       device indices handed out round robin (default "0")
#   WORK                output dir: out/{base,head}/<id>/{timeline.json,log}
#   VIDEOS              dir holding <id>.mp4 (default ~/.cache/curling_score/videos);
#                       hard-linked, else symlinked, never copied, never downloaded
#   SEED_PROXIES        dir of strip proxies to link in, if any (optional)
#   WEIGHTS             overhead detector (default <HEAD_SRC>/../weights/ds17b.pt
#                       if there is one, else $CURLING_SCORE_WEIGHTS as the
#                       interpreter sees it -- the worker image sets it)
#   COLD_IDS            ids that also run both sides against an empty detection
#                       cache of their own, so the panel readers really decode
#   LIVE_VIDEO          a cached video to run through `live-replay` on both
#                       sides (LIVE_SPEED, default 30, against real time; LIVE_RUNS,
#                       default "base head", may add "base2" as a control). The
#                       session calibrates and builds from however much of the
#                       recording exists when it looks, so a replay paced against
#                       the wall clock is not repeatable: pick a LIVE_SPEED fast
#                       enough that the whole file is written before it starts.
#   HASHSEED            PYTHONHASHSEED for both sides (default 0). segment._smooth
#                       breaks a tie with max(set(window), ...) over the strings
#                       "top"/"bottom", so one process's game boundaries can move
#                       a sample (5 s) from another's on the same input. Unpinned,
#                       an A/B of identical code differs about half the time.
#   NICE                nice level (default 10)
#   RESUME              1: keep a side's timeline already in WORK and run only
#                       what is missing (a failed run's log is kept as log.failed)
#
# `timeline.json` is compared with the lines that are the wall clock
# (source.analysed_at; for live-replay also live.wall_s) left out, after
# checking that both files exist, are non-empty, and hold exactly one
# analysed_at line; everything else must be byte-identical.
#
# Never calls YouTube: the VideoInfo is built locally (a fixed title, the
# file's own length) and handed to analyze(), whose download step finds the
# video already in the cache root. One shared cache root (videos, proxies,
# detections) serves both sides on the warm runs: the base fills it, the head
# must only read it.
set -uo pipefail

: "${BASE_SRC:?BASE_SRC}" "${HEAD_SRC:?HEAD_SRC}" "${WORK:?WORK}"
PYTHON=${PYTHON:-python3}
RUNNER=${RUNNER:-}
JOBS=${JOBS:-1}
GPUS=${GPUS:-0}
VIDEOS=${VIDEOS:-$HOME/.cache/curling_score/videos}
SEED_PROXIES=${SEED_PROXIES:-}
WEIGHTS=${WEIGHTS-}
if [ -z "$WEIGHTS" ] && [ -f "$(dirname "$HEAD_SRC")/weights/ds17b.pt" ]; then
  WEIGHTS=$(dirname "$HEAD_SRC")/weights/ds17b.pt
fi
COLD_IDS=${COLD_IDS:-}
LIVE_VIDEO=${LIVE_VIDEO:-}
LIVE_SPEED=${LIVE_SPEED:-30}
NICE=${NICE:-10}
HASHSEED=${HASHSEED:-0}
[ $# -gt 0 ] || [ -n "$LIVE_VIDEO" ] || { echo "usage: $0 <video id>..." >&2; exit 2; }

mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
status=0
read -ra GPU_LIST <<<"$GPUS"

# A hard link where the filesystem allows one, else a symlink to the real file.
link_into() {  # src dst
  [ -e "$2" ] || ln "$1" "$2" 2>/dev/null || ln -s "$(readlink -f "$1")" "$2"
}

# A cache root holding the videos and, when there are any, the proxies. The
# root is also where detections/ will appear.
make_root() {  # root id
  mkdir -p "$1/videos" "$1/proxies"
  link_into "$VIDEOS/$2.mp4" "$1/videos/$2.mp4"
  if [ -n "$SEED_PROXIES" ]; then
    for p in "$SEED_PROXIES/$2".*.mp4; do
      [ -e "$p" ] && link_into "$p" "$1/proxies/$(basename "$p")"
    done
  fi
  return 0
}

list_det() { ls "$1/detections" 2>/dev/null | grep '\.npz$' | sort; }

# The interpreter command for one job, as words.
prefix() {  # gpu
  local r=${RUNNER//@GPU@/$1}
  echo "$r nice -n $NICE $PYTHON"
}

run_side() {  # side src root id out gpu
  local side=$1 src=$2 root=$3 id=$4 out=$5 gpu=$6
  mkdir -p "$out"
  local t0=$SECONDS
  CURLING_SCORE_CACHE=$root PYTHONPATH=$src PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=$HASHSEED \
    $(prefix "$gpu") - "$side" "$src" "$root" "$id" "$out" "$WEIGHTS" \
    >"$out/log" 2>&1 <<'PY'
import os, sys, logging
side, src, root, vid, out, weights = sys.argv[1:7]
weights = weights or os.environ["CURLING_SCORE_WEIGHTS"]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
import curling_score
from pathlib import Path
here = Path(curling_score.__file__).resolve()
print(f"[{side}] curling_score imported from {here}", flush=True)
assert str(here).startswith(str(Path(src).resolve())), "imported the wrong tree"
print(f"[{side}] weights {weights} hashseed {os.environ.get('PYTHONHASHSEED')}", flush=True)
from curling_score import analyze as A
from curling_score.ingest import cache, source, frames
title = "Sheet 1"
path = cache.video_path(vid, Path(root))
assert path.is_file(), path
info = source.VideoInfo(
    video_id=vid, title=title, duration_s=float(frames.probe(path).duration_s),
    was_live=False, is_live=False, upload_date=None,
    sheet=source.sheet_from_title(title))
doc = A.analyze(source.canonical_url(vid), root=Path(root), info=info,
                weights=weights, progress=lambda m: print(m, flush=True))
print("wrote", A.write(doc, out), flush=True)
PY
  local rc=$?
  echo $((SECONDS - t0)) >"$out/wall_s"
  echo "[$side $id] rc=$rc wall=$((SECONDS - t0))s gpu=$gpu $(grep -m1 'imported from' "$out/log" | sed 's/.*imported from //')"
  return $rc
}

run_live() {  # side src root video out
  local side=$1 src=$2 root=$3 video=$4 out=$5
  mkdir -p "$out"
  local t0=$SECONDS gpu=${GPU_LIST[0]}
  CURLING_SCORE_CACHE=$root PYTHONPATH=$src PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=$HASHSEED \
    $(prefix "$gpu") -c "
import os, sys
import curling_score
print('[$side] curling_score imported from', curling_score.__file__, flush=True)
assert curling_score.__file__.startswith('$src'), 'imported the wrong tree'
from curling_score.cli import main
w = '$WEIGHTS' or os.environ['CURLING_SCORE_WEIGHTS']
sys.exit(main(['live-replay', '$video', '--out', '$out', '--speed', '$LIVE_SPEED', '--weights', w]))
" >"$out/log" 2>&1
  local rc=$?
  echo $((SECONDS - t0)) >"$out/wall_s"
  echo "[live $side] rc=$rc wall=$((SECONDS - t0))s $(grep -m1 'imported from' "$out/log" | sed 's/.*imported from //')"
  return $rc
}

# Every id on one side, JOBS at a time.
phase() {  # side src rootdir-for-id-fn outdir ids...
  local side=$1 src=$2 rootbase=$3 outbase=$4; shift 4
  local n=0 id
  for id in "$@"; do
    # RESUME=1: a side already done for this id is kept, so a run cut short (a
    # job that lost its GPU memory, say) goes on from where it stopped.
    if [ "${RESUME:-}" = 1 ] && [ -s "$outbase/$side/$id/timeline.json" ]; then
      echo "[$side $id] kept (resume)"; n=$((n + 1)); continue
    fi
    [ -s "$outbase/$side/$id/log" ] && mv "$outbase/$side/$id/log" "$outbase/$side/$id/log.failed"
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
    run_side "$side" "$src" "$rootbase" "$id" "$outbase/$side/$id" \
      "${GPU_LIST[$((n % ${#GPU_LIST[@]}))]}" &
    n=$((n + 1))
  done
  wait
}

# Both timelines present and non-empty, one analysed_at line each; then equal
# once the wall-clock lines are out.
MASK='"analysed_at"'
compare() {  # label id base_out head_out
  local b=$3/timeline.json h=$4/timeline.json f
  for f in "$b" "$h"; do
    if [ ! -s "$f" ]; then echo "FAIL  $1 $2: no timeline at $f"; status=1; return; fi
    if [ "$(grep -c '"analysed_at"' "$f")" != 1 ]; then
      echo "FAIL  $1 $2: $f does not have exactly one analysed_at line"; status=1; return
    fi
  done
  if cmp -s <(grep -Ev "$MASK" "$b") <(grep -Ev "$MASK" "$h"); then
    echo "same  $1 $2"
  else
    echo "DIFF  $1 $2"; status=1
    diff <(grep -Ev "$MASK" "$b") <(grep -Ev "$MASK" "$h") | head -20
  fi
}

IDS=("$@")
ROOT=$WORK/root
for id in "${IDS[@]}"; do make_root "$ROOT" "$id"; done

if [ ${#IDS[@]} -gt 0 ]; then
  phase base "$BASE_SRC" "$ROOT" "$WORK/out" "${IDS[@]}"
  list_det "$ROOT" >"$WORK/det.after-base"
  phase head "$HEAD_SRC" "$ROOT" "$WORK/out" "${IDS[@]}"
  list_det "$ROOT" >"$WORK/det.after-head"
  new=$(comm -13 "$WORK/det.after-base" "$WORK/det.after-head" | wc -l)
  for id in "${IDS[@]}"; do compare warm "$id" "$WORK/out/base/$id" "$WORK/out/head/$id"; done
  echo "cache entries: after base $(wc -l <"$WORK/det.after-base"), after head $(wc -l <"$WORK/det.after-head"), new from head $new"
  [ "$new" -eq 0 ] || { status=1; comm -13 "$WORK/det.after-base" "$WORK/det.after-head"; }
fi

# Each side against its own empty detection cache: nothing is replayed.
for id in $COLD_IDS; do
  for side in base head; do
    croot=$WORK/root-cold-$side
    make_root "$croot" "$id"
    # the proxy the warm run built (or linked), shared by link
    for p in "$ROOT/proxies/$id".*.mp4; do
      [ -e "$p" ] && link_into "$p" "$croot/proxies/$(basename "$p")"
    done
  done
  for side in base head; do
    src=$BASE_SRC; [ $side = head ] && src=$HEAD_SRC
    run_side "$side" "$src" "$WORK/root-cold-$side" "$id" "$WORK/out-cold/$side/$id" \
      "${GPU_LIST[$(( $( [ $side = head ] && echo 1 || echo 0 ) % ${#GPU_LIST[@]} ))]}" &
  done
  wait
  compare cold "$id" "$WORK/out-cold/base/$id" "$WORK/out-cold/head/$id"
  diff <(list_det "$WORK/root-cold-base") <(list_det "$WORK/root-cold-head") >/dev/null \
    && echo "cold $id: detection entries $(list_det "$WORK/root-cold-base" | wc -l) each side, same names" \
    || { echo "cold $id: detection entry names DIFFER"; status=1; }
  rm -rf "$WORK/root-cold-base/detections" "$WORK/root-cold-head/detections"
done

if [ -n "$LIVE_VIDEO" ]; then
  lroot=$WORK/root-live; mkdir -p "$lroot"
  MASK='"analysed_at"|"wall_s"'
  for side in ${LIVE_RUNS:-base head}; do
    src=$BASE_SRC; case $side in head*) src=$HEAD_SRC;; esac
    run_live "$side" "$src" "$lroot" "$LIVE_VIDEO" "$WORK/out-live/$side" || status=1
  done
  compare live "$(basename "$LIVE_VIDEO")" "$WORK/out-live/base" "$WORK/out-live/head"
  # A second run of one side, if asked for: what the replay's own timing does.
  [ -d "$WORK/out-live/base2" ] && compare live-control "$(basename "$LIVE_VIDEO") base vs base" \
    "$WORK/out-live/base" "$WORK/out-live/base2"
  echo "live publishes: base $(ls "$WORK/out-live/base/publishes" 2>/dev/null | wc -l), head $(ls "$WORK/out-live/head/publishes" 2>/dev/null | wc -l)"
fi
exit $status
