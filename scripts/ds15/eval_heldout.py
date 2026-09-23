#!/usr/bin/env python3
"""Does a model now see the late stone, on the game it never trained on?

For each ds15 fold, on that fold's held-out frames: the target is the
reviewer's box of the arriving stone -- the hand label of the right colour
nearest where the manifest said to look (within ``NEAR_PX``); a frame whose
stone the reviewer could not see has no target and is skipped. A model finds
it when it predicts a box of that colour overlapping it (IoU >= 0.5) at the
pipeline's confidence (0.30, imgsz 448). Scored for ds11a and for the fold's
own model, so the difference is the frames' doing. Also counts predictions
that match no hand label at all, the price of any gain.

Runs on the GPU box (needs ultralytics):

    python eval_heldout.py --folds /data/wdd/curling/ds15/folds \
        --manifest manifest.json --runs /data/wdd/curling/runs \
        --baseline /data/wdd/curling_score/weights/ds11a.pt
"""

import argparse
import json
import math
from pathlib import Path

from ultralytics import YOLO

GAMES = ("AEqLTgM25Tc", "VXU9xwmugRg", "hOKZoeJNTpM")
CLS = {"red": 0, "yellow": 1}
NEAR_PX = 45
CONF, IMGSZ = 0.30, 448


def iou(a, b):
    ax0, ay0, ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx0, by0, bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw, ih = max(0, min(ax1, bx1) - max(ax0, bx0)), max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter) if inter else 0.0


def boxes(path, W, H):
    out = []
    for line in path.read_text().split("\n") if path.exists() else ():
        p = line.split()
        if len(p) == 5:
            c, cx, cy, w, h = int(p[0]), *map(float, p[1:])
            out.append((c, (cx * W, cy * H, w * W, h * H)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    a = ap.parse_args()
    frames = {f["stem"]: (r, f) for r in json.loads(a.manifest.read_text())["rocks"]
              for f in r["frames"]}
    base = YOLO(str(a.baseline))
    table = {}
    for g in GAMES:
        fold = YOLO(str(a.runs / f"ds15-{g}" / "weights" / "best.pt"))
        d = a.folds / g
        score = {"frames": 0, "targets": 0, "ds11a": 0, "fold": 0,
                 "ds11a_unmatched": 0, "fold_unmatched": 0}
        for img in sorted((d / "images/heldout").glob("*.jpg")):
            rock, fr = frames[img.stem]
            res = {"ds11a": base.predict(str(img), conf=CONF, imgsz=IMGSZ, verbose=False)[0],
                   "fold": fold.predict(str(img), conf=CONF, imgsz=IMGSZ, verbose=False)[0]}
            H, W = res["fold"].orig_shape
            truth = boxes(d / "labels/heldout" / f"{img.stem}.txt", W, H)
            score["frames"] += 1
            ex, ey = fr["expect_px"]
            cands = [(math.hypot(b[0] - ex, b[1] - ey), b) for c, b in truth
                     if c == CLS[rock["color"]]]
            target = min(cands)[1] if cands and min(cands)[0] <= NEAR_PX else None
            if target:
                score["targets"] += 1
            for name, r in res.items():
                preds = [(int(c), tuple(b)) for c, b in zip(r.boxes.cls.tolist(), r.boxes.xywh.tolist())]
                if target and any(c == CLS[rock["color"]] and iou(b, target) >= 0.5 for c, b in preds):
                    score[name] += 1
                score[f"{name}_unmatched"] += sum(
                    1 for c, b in preds if not any(c == tc and iou(b, tb) >= 0.5 for tc, tb in truth))
        table[g] = score
        print(g, score, flush=True)
    tot = {k: sum(s[k] for s in table.values()) for k in next(iter(table.values()))}
    print("all", tot)


if __name__ == "__main__":
    main()
