"""Score handle-point models against labelled frames none of them trained on.

    ./.venv/bin/python scripts/spin/eval_points.py --wave ~/curling-work/spin/wave1 \\
        --labels labels.json --stems stems.txt p1=path/to/best.pt p2=path/to/best.pt

``--labels`` is the merged {points, flags} (serve.py's GET /labels); ``--stems``
the frames to score, one per line. For each model and colour: the neck's and the
grip end's distance from the person's, and the error in what the rate is read
from -- s, the grip's offset across from the neck -- all in stone widths, plus
how often the grip is put on the wrong side of the neck where the person put it
clearly to one side.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

CLEAR_SIDE = 0.08    # |s| beyond this, in stone widths, is clearly to one side


def predict(model, wave, stems):
    out = {}
    ims = [np.asarray(Image.open(Path(wave) / "images" / f"{s}.png").convert("RGB"))[:, :, ::-1] for s in stems]
    for i in range(0, len(stems), 64):
        for stem, r in zip(stems[i:i + 64], model.predict(ims[i:i + 64], imgsz=224, conf=0.25, verbose=False)):
            if r.keypoints is None or len(r.boxes) == 0:
                continue
            k = int(np.argmax(r.boxes.conf.cpu().numpy()))
            out[stem] = r.keypoints.xy.cpu().numpy()[k]
    return out


def score(items, labels, preds, stems):
    rows = []
    for s in stems:
        it, pt = items[s], labels[s]
        x0, _, x1, _ = it["stone_box"]
        w = x1 - x0
        if s not in preds:
            rows.append(dict(color=it["color"], missed=True))
            continue
        p = preds[s]
        neck, grip = np.array(pt["neck"]), np.array(pt["grip"])
        s_true = (grip[0] - neck[0]) / w
        s_pred = (p[1, 0] - p[0, 0]) / w
        rows.append(dict(color=it["color"], missed=False,
                         neck=float(np.linalg.norm(p[0] - neck) / w), grip=float(np.linalg.norm(p[1] - grip) / w),
                         ds=float(abs(s_pred - s_true)),
                         wrong_side=bool(abs(s_true) > CLEAR_SIDE and np.sign(s_pred) != np.sign(s_true)),
                         clear=bool(abs(s_true) > CLEAR_SIDE)))
    return rows


def summary(rows):
    got = [r for r in rows if not r["missed"]]
    if not got:
        return "no frames"
    clear = [r for r in got if r["clear"]]
    return (f"n={len(rows):3d} missed {len(rows) - len(got)}  neck {np.median([r['neck'] for r in got]):.3f}  "
            f"grip {np.median([r['grip'] for r in got]):.3f} (90% {np.percentile([r['grip'] for r in got], 90):.3f})  "
            f"s err {np.median([r['ds'] for r in got]):.3f}  wrong side {sum(r['wrong_side'] for r in clear)}/{len(clear)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--stems", required=True)
    ap.add_argument("models", nargs="+", help="NAME=weights.pt")
    args = ap.parse_args()
    from ultralytics import YOLO

    wave = Path(args.wave).expanduser()
    items = {it["stem"]: it for it in json.loads((wave / "items.json").read_text())}
    labels = json.loads(Path(args.labels).read_text())["points"]
    stems = [s for s in Path(args.stems).read_text().split() if s in labels]
    for spec in args.models:
        name, path = spec.split("=", 1)
        rows = score(items, labels, predict(YOLO(path), wave, stems), stems)
        for colour in ("yellow", "red"):
            print(f"{name:4s} {colour:6s} {summary([r for r in rows if r['color'] == colour])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
