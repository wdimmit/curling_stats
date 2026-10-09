#!/usr/bin/env python
"""Does a ds17 model stop boxing the sliding thrower, and keep the stone?

On every reviewed ds17 frame, each model's predictions at the pipeline's
settings (conf 0.30, imgsz 448) against the hand labels (same class, IoU >= 0.5):
true positives are the stones kept, false negatives the stones lost, and every
unmatched prediction is a box on something that is not a stone -- on these
frames, nearly always the thrower. Split into the held-out games' frames and
the rest. The portrait, the toque and the benchmarks are ds16's eval.py, run
as it is; the hats over whole games are mine_hats.py with the model's weights.

    python eval.py --manifest manifest.json --set /ds17/build/set \
        --models ds16a=/opt/curling/weights/ds16a.pt held=/runs/ds17-heldout/weights/best.pt \
        --held _OGoqF5W9-I 0SWB4g3SJoE
"""

import argparse
import json
from pathlib import Path

import cv2
from ultralytics import YOLO

CONF, IMGSZ = 0.30, 448


def iou(a, b):
    ax0, ay0, ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx0, by0, bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw, ih = max(0, min(ax1, bx1) - max(ax0, bx0)), max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter) if inter else 0.0


def hand(path, W, H):
    out = []
    for line in path.read_text().split("\n") if path.exists() else ():
        p = line.split()
        if len(p) == 5:
            out.append((int(p[0]), (float(p[1]) * W, float(p[2]) * H, float(p[3]) * W, float(p[4]) * H)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--models", nargs="+", required=True, help="name=weights")
    ap.add_argument("--held", nargs="+", default=["_OGoqF5W9-I", "0SWB4g3SJoE"])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    models = {k: YOLO(v) for k, v in (m.split("=", 1) for m in a.models)}
    frames = json.loads(a.manifest.read_text())["frames"]
    report = {}
    for name, model in models.items():
        acc = {}
        for f in frames:
            img = cv2.imread(str(a.set / "images/train" / f"{f['stem']}.jpg"))
            if img is None:
                continue
            H, W = img.shape[:2]
            truth = hand(a.set / "labels/train" / f"{f['stem']}.txt", W, H)
            r = model.predict(img, conf=CONF, imgsz=IMGSZ, verbose=False)[0]
            pred = [(int(c), tuple(b)) for c, b in zip(r.boxes.cls.tolist(), r.boxes.xywh.tolist())]
            used, tp, fp = set(), 0, 0
            for c, b in pred:
                hit = next((i for i, (tc, tb) in enumerate(truth)
                            if i not in used and tc == c and iou(b, tb) >= 0.5), None)
                if hit is None:
                    fp += 1
                else:
                    used.add(hit)
                    tp += 1
            g = acc.setdefault("held-out" if f["video"] in a.held else "trained-on",
                               {"frames": 0, "stones": 0, "kept": 0, "lost": 0, "false_boxes": 0,
                                "frames_with_false_box": 0})
            g["frames"] += 1
            g["stones"] += len(truth)
            g["kept"] += tp
            g["lost"] += len(truth) - len(used)
            g["false_boxes"] += fp
            g["frames_with_false_box"] += fp > 0
        report[name] = acc
        print(name, json.dumps(acc), flush=True)
    if a.out:
        a.out.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
