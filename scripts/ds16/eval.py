#!/usr/bin/env python
"""Does a ds16 model stop seeing the portrait and the toque, and nothing else?

Three measures, every model against ds15a, all at the pipeline's settings
(conf 0.30, imgsz 448):

* frames: on each reviewed ds16 frame, predictions against the hand labels
  (same class, IoU >= 0.5), split into the held-out game's frames and the rest,
  with the unmatched predictions that sit on the portrait (within PORTRAIT_PX of
  the manifest's tee) and those on a toque frame counted apart.
* video: every VIDEO_EVERY_S through the held-out game's two house panels, the
  share of frames holding a prediction within TEE_M of the tee. ds15a's is
  ~97% (the portrait); a model that has learnt it should be left with the
  moments a real stone rests there.
* benchmarks: mAP50 and mAP50-95 on ds11 val and ds12, which ds15a scored
  0.993 and 0.988 / 0.939 and must not lose.

    python eval.py --manifest manifest.json --set /ds16/build/set \
        --models ds15a=/opt/curling/weights/ds15a.pt fold=/runs/ds16-8un0-JBKkLA/weights/best.pt \
        --held 8un0-JBKkLA --timeline /ds16/timelines/tl_s_11MefKj8rSvZiFrlA.json \
        --video /data/cache/videos/8un0-JBKkLA.mp4 --bench ds11=/folds/all/curling.yaml ds12=/ds12/curling.yaml
"""

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from ultralytics import YOLO

CONF, IMGSZ = 0.30, 448
PORTRAIT_PX = 12
TEE_M = 0.06
VIDEO_EVERY_S = 10.0


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
            c, cx, cy, w, h = int(p[0]), *map(float, p[1:])
            out.append((c, (cx * W, cy * H, w * W, h * H)))
    return out


def predict(model, img):
    r = model.predict(img, conf=CONF, imgsz=IMGSZ, verbose=False)[0]
    return [(int(c), tuple(b)) for c, b in zip(r.boxes.cls.tolist(), r.boxes.xywh.tolist())]


def score_frames(models, frames, set_dir):
    import cv2
    rows = {}
    for name, model in models.items():
        acc = {}
        for f in frames:
            img = cv2.imread(str(set_dir / "images/train" / f"{f['stem']}.jpg"))
            H, W = img.shape[:2]
            truth = hand(set_dir / "labels/train" / f"{f['stem']}.txt", W, H)
            pred = predict(model, img)
            used = set()
            tp = fp = portrait = toque = 0
            for c, b in pred:
                hit = next((i for i, (tc, tb) in enumerate(truth)
                            if i not in used and tc == c and iou(b, tb) >= 0.5), None)
                if hit is not None:
                    used.add(hit)
                    tp += 1
                    continue
                fp += 1
                if f["sheet"] == 4 and np.hypot(b[0] - f["tee_px"][0], b[1] - f["tee_px"][1]) <= PORTRAIT_PX:
                    portrait += 1
                elif f["kind"] == "toque" and c == 1:
                    toque += 1
            group = "held-out" if f["held"] else "trained-on"
            a = acc.setdefault(group, {"frames": 0, "labels": 0, "tp": 0, "fp": 0, "fn": 0,
                                       "portrait_fp": 0, "toque_fp": 0})
            a["frames"] += 1
            a["labels"] += len(truth)
            a["tp"] += tp
            a["fp"] += fp
            a["fn"] += len(truth) - len(used)
            a["portrait_fp"] += portrait
            a["toque_fp"] += toque
        rows[name] = acc
    return rows


def video_frames(video, rect, every):
    x, y, w, h = rect
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
           "-vf", f"fps=1/{every},crop={w}:{h}:{x}:{y}:exact=1", "-f", "rawvideo",
           "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)


def score_video(models, doc, video):
    out = {}
    for panel in ("top", "bottom"):
        cal = doc["calibration"][panel]
        frames = video_frames(video, cal["rect"], VIDEO_EVERY_S)
        cx, cy = cal["center_px"]
        r_px = TEE_M * cal["px_per_m"]
        for name, model in models.items():
            on_tee = stones = 0
            for img in frames:
                pred = predict(model, img)
                stones += len(pred)
                on_tee += any(np.hypot(b[0] - cx, b[1] - cy) <= r_px for _c, b in pred)
            out.setdefault(name, {})[panel] = {"frames": len(frames), "on_tee": on_tee,
                                               "share": round(on_tee / max(1, len(frames)), 3),
                                               "stones_per_frame": round(stones / max(1, len(frames)), 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--models", nargs="+", required=True, help="name=weights")
    ap.add_argument("--held", nargs="+", default=["8un0-JBKkLA"])
    ap.add_argument("--timeline", type=Path, help="the held-out game's timeline, for its calibration")
    ap.add_argument("--video", type=Path, help="the held-out game's video")
    ap.add_argument("--bench", nargs="*", default=[], help="name=data.yaml")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    models = {k: YOLO(v) for k, v in (m.split("=", 1) for m in a.models)}
    reviewed = {p.stem for p in (a.set / "images/train").glob("*.jpg")}
    frames = [dict(f, held=f["video"] in a.held)
              for f in json.loads(a.manifest.read_text())["frames"] if f["stem"] in reviewed]
    report = {"frames": score_frames(models, frames, a.set)}
    print(json.dumps(report["frames"], indent=1), flush=True)
    if a.timeline and a.video:
        report["video"] = score_video(models, json.loads(a.timeline.read_text()), a.video)
        print(json.dumps(report["video"], indent=1), flush=True)
    bench = {}
    for spec in a.bench:
        bname, data = spec.split("=", 1)
        for name, model in models.items():
            m = model.val(data=data, imgsz=640, batch=16, verbose=False, plots=False)
            bench.setdefault(bname, {})[name] = {"map50": round(float(m.box.map50), 4),
                                                 "map50_95": round(float(m.box.map), 4)}
    report["benchmarks"] = bench
    print(json.dumps(bench, indent=1))
    if a.out:
        a.out.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
