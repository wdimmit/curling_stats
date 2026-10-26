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
# Parameters (environment):
#   BASE_SRC, HEAD_SRC  the two `src` trees; each side imports only its own
#   PYTHON              interpreter with the dependencies (default: python3)
#   WORK                output dir: out/{base,head}/<id>/{timeline.json,log}
#   VIDEOS              dir holding <id>.mp4 (default ~/.cache/curling_score/videos);
#                       hard-linked, never copied, never downloaded
#   SEED_PROXIES        dir of strip proxies to hard-link in, if any (optional)
#   WEIGHTS             overhead detector (default <HEAD_SRC>/../weights/ds17b.pt)
#   COLD_IDS            ids that also run both sides against an empty detection
#                       cache of their own, so the panel readers really decode
#   NICE                nice level (default 10)
#   HASHSEED            PYTHONHASHSEED for both sides (default 0). segment._smooth
#                       breaks a tie with max(set(window), ...) over the strings
#                       "top"/"bottom", so one process's game boundaries can move
#                       a sample (5 s) from another's on the same input. Unpinned,
#                       an A/B of identical code differs about half the time.
#
# `timeline.json` is compared with the one line that is the wall clock
# (source.analysed_at) left out; everything else must be byte-identical.
#
# Never calls YouTube: the VideoInfo is built locally (a fixed title, the
# file's own length) and handed to analyze(), whose download step finds the
# video already in the cache root. Run one id at a time -- an analyze holds
# several GB. One shared cache root (videos, proxies, detections) serves both
# sides on the warm runs: the base fills it, the head must only read it.
set -uo pipefail

: "${BASE_SRC:?BASE_SRC}" "${HEAD_SRC:?HEAD_SRC}" "${WORK:?WORK}"
PYTHON=${PYTHON:-python3}
VIDEOS=${VIDEOS:-$HOME/.cache/curling_score/videos}
SEED_PROXIES=${SEED_PROXIES:-}
WEIGHTS=${WEIGHTS:-$(dirname "$HEAD_SRC")/weights/ds17b.pt}
COLD_IDS=${COLD_IDS:-}
NICE=${NICE:-10}
HASHSEED=${HASHSEED:-0}
[ $# -gt 0 ] || { echo "usage: $0 <video id>..." >&2; exit 2; }

mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
status=0

# A cache root holding the videos (hard links: no 2 GB copies) and, when there
# are any, the proxies. The root is also where detections/ will appear.
make_root() {  # root id
  mkdir -p "$1/videos" "$1/proxies"
  [ -e "$1/videos/$2.mp4" ] || ln "$VIDEOS/$2.mp4" "$1/videos/$2.mp4" 2>/dev/null \
    || ln -s "$VIDEOS/$2.mp4" "$1/videos/$2.mp4"
  [ -n "$SEED_PROXIES" ] && for p in "$SEED_PROXIES/$2".*.mp4; do
    [ -e "$p" ] && [ ! -e "$1/proxies/$(basename "$p")" ] \
      && ln "$p" "$1/proxies/$(basename "$p")"
  done
  return 0
}

entries() { ls "$1/detections" 2>/dev/null | grep -c '\.npz$' || true; }

run_side() {  # side src root id out
  local side=$1 src=$2 root=$3 id=$4 out=$5
  mkdir -p "$out"
  local t0=$SECONDS
  CURLING_SCORE_CACHE=$root PYTHONPATH=$src PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=$HASHSEED \
    nice -n "$NICE" "$PYTHON" - "$side" "$src" "$root" "$id" "$out" "$WEIGHTS" \
    >"$out/log" 2>&1 <<'PY'
import sys, logging
side, src, root, vid, out, weights = sys.argv[1:7]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
import curling_score
from pathlib import Path
here = Path(curling_score.__file__).resolve()
print(f"[{side}] curling_score imported from {here}", flush=True)
assert str(here).startswith(str(Path(src).resolve())), "imported the wrong tree"
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
  echo "[$side $id] rc=$rc wall=$((SECONDS - t0))s imports: $(grep -m1 'imported from' "$out/log")"
  return $rc
}

compare() {  # label id base_out head_out
  if cmp -s <(grep -v '"analysed_at"' "$3/timeline.json") \
            <(grep -v '"analysed_at"' "$4/timeline.json"); then
    echo "same  $1 $2"
  else
    echo "DIFF  $1 $2"; status=1
    diff <(grep -v '"analysed_at"' "$3/timeline.json") \
         <(grep -v '"analysed_at"' "$4/timeline.json") | head -20
  fi
}

ROOT=$WORK/root
for id in "$@"; do
  make_root "$ROOT" "$id"
  base_out=$WORK/out/base/$id head_out=$WORK/out/head/$id
  run_side base "$BASE_SRC" "$ROOT" "$id" "$base_out" || { echo "FAIL base $id"; status=1; continue; }
  b=$(entries "$ROOT"); ls "$ROOT/detections" 2>/dev/null | sort >"$WORK/det.$id.base"
  run_side head "$HEAD_SRC" "$ROOT" "$id" "$head_out" || { echo "FAIL head $id"; status=1; continue; }
  h=$(entries "$ROOT"); ls "$ROOT/detections" 2>/dev/null | sort >"$WORK/det.$id.head"
  new=$(comm -13 "$WORK/det.$id.base" "$WORK/det.$id.head" | wc -l)
  compare warm "$id" "$base_out" "$head_out"
  echo "cache entries $id: after base $b, after head $h, new from head $new"
  [ "$new" -eq 0 ] || status=1

  case " $COLD_IDS " in *" $id "*)
    # Each side against its own empty detection cache: nothing is replayed.
    for side in base head; do
      croot=$WORK/root-cold-$side
      make_root "$croot" "$id"
      # the proxy the warm run built, shared by link
      for p in "$ROOT/proxies/$id".*.mp4; do
        [ -e "$p" ] && [ ! -e "$croot/proxies/$(basename "$p")" ] && ln "$p" "$croot/proxies/$(basename "$p")"
      done
      src=$BASE_SRC; [ $side = head ] && src=$HEAD_SRC
      run_side "$side" "$src" "$croot" "$id" "$WORK/out-cold/$side/$id" \
        || { echo "FAIL cold $side $id"; status=1; }
    done
    compare cold "$id" "$WORK/out-cold/base/$id" "$WORK/out-cold/head/$id"
    echo "cache entries $id cold: base $(entries "$WORK/root-cold-base"), head $(entries "$WORK/root-cold-head")"
    ;;
  esac
done
exit $status
