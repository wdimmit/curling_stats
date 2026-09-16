"""The only evaluation that decides anything: what does it do on frames nobody
labelled?

The val mAP is not evidence. 33 instances from two videos of the same rink,
the same camera and the same crop will score high whatever the model has
actually learned, and ds11's README is the standing warning: ds3 scored mAP50
0.977 while finding 11 of 17 hand-observed deliveries, because its labels and
its validation came from the same flawed opinion.

So this runs the model over candidates that were never labelled, draws what it
says, and reports the numbers a person can check by looking: how many frames
got a box, how many boxes per frame, and how each box compares with the width
the perspective solve predicts for its row -- an independent yardstick the
model never saw.
"""
import json
import random
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/home/tcuser/src/curling_score/.claude/worktrees/longview/src")

from curling_score.detect import longview
from curling_score.harvest import sidepool, sideviews as SV
from curling_score.train import boxedit
from ultralytics import YOLO

WORK = Path("/home/tcuser/curling-work/ds13")
S = Path("/tmp/claude-1000/-home-tcuser-src-curling-score/"
         "58886916-90ab-4946-881c-54d52bb8f237/scratchpad")
OUT = WORK / "eval"
OUT.mkdir(exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()

weights = WORK / "runs" / "wave1" / "weights" / "best.pt"
model = YOLO(str(weights))
views_doc = json.loads((S / "wave1" / "ds13_cached_views.json").read_text())

# Everything already labelled, so none of it can flatter the result.
labelled = set()
for p in sorted((WORK / "edits").glob("*.json")):
    labelled |= set(json.loads(p.read_text()).get("boxes", {}))
labelled |= set(json.loads(
    Path("/home/tcuser/src/curling_score/ds13-wave1-cropped50-boxes.json")
    .read_text()).get("boxes", {}))
print(f"excluding {len(labelled)} labelled frames", flush=True)

sample = json.loads((S / "wave1" / "eval_rows.json").read_text())
print(f"sampling {len(sample)} unlabelled candidates", flush=True)

CACHE = S / "eval_full"
CACHE.mkdir(exist_ok=True)
tiles, stats, ratios = [], Counter(), []
for r in sample:
    stem, vid = r["stem"], r["video_id"]
    src = CACHE / f"{stem}.jpg"
    if not src.is_file():
        stats["missing"] += 1
        continue
    vv = SV.from_json(views_doc[vid])
    view = dict(SV.usable_views(vv))[r["view"]]
    y0, y1 = sidepool.band_crop(view)
    img = cv2.imread(str(src))
    lo, hi = max(0, y0), min(img.shape[0], y1)
    crop = img[lo:hi, :].copy()
    geom = boxedit.frame_geometry(view, longview.STONE_WIDTH_AT_HOG_PX,
                                  row_offset=lo)

    res = model.predict(crop, imgsz=800, conf=0.35, verbose=False)[0]
    n = 0
    for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(),
                        res.boxes.cls.cpu().numpy(),
                        res.boxes.conf.cpu().numpy()):
        x0, yy0, x1, yy1 = b
        w = x1 - x0
        expect = geom["k"] * (yy1 - geom["yh"])
        ratios.append(w / expect if expect > 1 else 99.0)
        col = (60, 60, 235) if int(c) == 0 else (40, 200, 235)
        cv2.rectangle(crop, (int(x0), int(yy0)), (int(x1), int(yy1)), col, 2)
        cv2.putText(crop, f"{cf:.2f} {w / expect:.2f}w",
                    (int(x0), max(12, int(yy0) - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        n += 1
    stats[f"{n} box"] += 1
    t = cv2.resize(crop, (700, 170))
    pad = np.full((190, 700, 3), 22, np.uint8)
    pad[:170] = t
    cv2.putText(pad, f"{stem}  {n} box  [{r['position']}]", (4, 185),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1)
    tiles.append(pad)

import math
for s in range(math.ceil(len(tiles) / 10)):
    ch = tiles[s * 10:(s + 1) * 10]
    while len(ch) < 10:
        ch.append(np.full((190, 700, 3), 22, np.uint8))
    cv2.imwrite(str(OUT / f"eval_{s + 1}.png"), np.vstack(ch))

print("frames by box count:", dict(stats), flush=True)
if ratios:
    import statistics
    inb = [r for r in ratios if 0.5 <= r <= 2.0]
    print(f"boxes {len(ratios)}; width/geometric-expectation median "
          f"{statistics.median(ratios):.2f}, "
          f"{len(inb)}/{len(ratios)} within 0.5-2.0x", flush=True)
print("sheets:", math.ceil(len(tiles) / 10), flush=True)
