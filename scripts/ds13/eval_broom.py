"""Score side models on the broom wave's held-out frames: how far each box is
from the stone a person boxed alone, broom or shoe beside it.

    ./.venv/bin/python scripts/ds13/eval_broom.py ~/curling-work/ds13broom/tree-broom1 \\
        datasets/ds13/manifest-broom1.json ds13c=weights/ds13c.pt ds13d=path/to/best.pt

Per model, over every labelled stone in the held-out frames, by the frame's
``pick`` (the broom wave's wide: ds13c's box had widened there; random:
anywhere along the band; the kink wave's hack, slide, edge, hog): matched to
the model's same-colour box of best IoU (>= 0.3) at the pipeline's 0.25 cut,
the centre's error across and the box's width over the label's, in px, and
the stones it missed, and the bottom edge's error, in px. The centre across
and the bottom edge (the distance along: 6-12 cm a row) are what a delivery
dot is made of.
"""
import json
import sys
from pathlib import Path

import numpy as np

MATCH_IOU, CONF = 0.3, 0.25


def boxes_of(path: Path, w: int, h: int):
    out = []
    if path.exists():
        for ln in path.read_text().split("\n"):
            if ln.strip():
                c, cx, cy, bw, bh = (float(v) for v in ln.split())
                out.append((int(c), (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h))
    return out


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def main(argv) -> int:
    tree, manifest = Path(argv[1]).expanduser(), Path(argv[2])
    models = dict(a.split("=", 1) for a in argv[3:])
    pick = {m["stem"]: m["pick"] for m in json.loads(manifest.read_text())}
    images = sorted((tree / "images" / "heldout").glob("*.jpg"))
    from ultralytics import YOLO
    import cv2
    for name, weights in models.items():
        model = YOLO(str(Path(weights).expanduser()))
        picks = sorted(set(pick.values()))
        rows = {k: [] for k in picks}
        missed = {k: 0 for k in picks}
        for img_path in images:
            img = cv2.imread(str(img_path))
            h, w = img.shape[:2]
            truth = boxes_of(tree / "labels" / "heldout" / (img_path.stem + ".txt"), w, h)
            res = model.predict(img, imgsz=800, conf=CONF, verbose=False)[0]
            pred = [(int(c), *map(float, b)) for b, c in
                    zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy())] if res.boxes is not None else []
            k = pick.get(img_path.stem, "random")
            for t in truth:
                same = [p for p in pred if p[0] == t[0]]
                best = max(same, key=lambda p: iou(t[1:], p[1:]), default=None)
                if best is None or iou(t[1:], best[1:]) < MATCH_IOU:
                    missed[k] += 1
                    continue
                dcx = (best[1] + best[3]) / 2 - (t[1] + t[3]) / 2
                dw = (best[3] - best[1]) - (t[3] - t[1])
                dbot = best[4] - t[4]
                rows[k].append((abs(dcx), dw, iou(t[1:], best[1:]), abs(dbot)))
        for k in picks:
            a = np.array(rows[k]) if rows[k] else np.zeros((0, 4))
            if not len(a):
                print(f"{name} {k}: no matched stones, {missed[k]} missed"); continue
            print(f"{name} {k}: {len(a)} stones, {missed[k]} missed; centre across |err| median "
                  f"{np.median(a[:, 0]):.2f} p90 {np.percentile(a[:, 0], 90):.2f} px; width over the "
                  f"label median {np.median(a[:, 1]):+.2f} p90 {np.percentile(a[:, 1], 90):+.2f} px; "
                  f"bottom |err| median {np.median(a[:, 3]):.2f} p90 {np.percentile(a[:, 3], 90):.2f} px; "
                  f"IoU median {np.median(a[:, 2]):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
