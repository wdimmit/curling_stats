#!/usr/bin/env python
"""cin1: what ds17b boxes in Cincinnati's two houses, every few seconds of a stream.

Cincinnati's red house is painted with a sponsor's lettering in the 12-to-8
band, and ds17b reads it as red stones -- three that never leave, so the house
never empties and its ends run together (rKiMmtVod0I, 2026-10-26). This walks
the whole stream through both overhead panels with the production model at the
pre-label threshold and keeps every box, with where it sits on the sheet, for
`build.py` to choose the review frames from.

    python scripts/cin1/mine.py VIDEO --timeline timeline.json --weights ds17b.pt --out mined.json
"""

import argparse
import json
import math
from pathlib import Path

CONF, IMGSZ, EVERY_S = 0.05, 448, 2.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--timeline", type=Path, required=True, help="for the panels' calibration")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--every", type=float, default=EVERY_S)
    a = ap.parse_args()

    from curling_score.detect import yolo
    from curling_score.geometry.calibrate import PanelCalib
    from curling_score.ingest import frames as F

    doc = json.loads(a.timeline.read_text())
    cal = {name: doc["calibration"][name] for name in ("top", "bottom")}
    calibs = {name: PanelCalib(center_px=tuple(c["center_px"]), px_per_m=c["px_per_m"], edge_erosion_px=0.0,
                               residual_m=c["residual_m"], flipped=c["flipped"],
                               aspect=c.get("aspect", 1.0)) for name, c in cal.items()}
    rects = {name: c["rect"] for name, c in cal.items()}
    x0 = min(r[0] for r in rects.values())
    y0 = min(r[1] for r in rects.values())
    x1 = max(r[0] + r[2] for r in rects.values())
    y1 = max(r[1] + r[3] for r in rects.values())
    det = yolo.YoloDetector(a.weights, conf=CONF, imgsz=IMGSZ, device=0)

    out = []
    duration = doc["source"].get("duration_s") or 1e9
    for t, strip in F.window(a.video, 0.0, duration, fps=1.0 / a.every, crop=(x0, y0, x1 - x0, y1 - y0)):
        crops = {name: strip[r[1] - y0:r[1] - y0 + r[3], r[0] - x0:r[0] - x0 + r[2]] for name, r in rects.items()}
        results = det.model.predict(list(crops.values()), conf=CONF, imgsz=IMGSZ, device=0, verbose=False)
        for (name, _img), res in zip(crops.items(), results):
            boxes = []
            for k, cf, xy in zip(res.boxes.cls.tolist(), res.boxes.conf.tolist(), res.boxes.xyxy.tolist()):
                sx, sy = calibs[name].to_sheet((xy[0] + xy[2]) / 2, (xy[1] + xy[3]) / 2)
                boxes.append({"color": ("red", "yellow")[int(k)], "conf": round(cf, 3),
                              "xyxy": [round(v, 1) for v in xy], "x": round(sx, 3), "y": round(sy, 3),
                              "r": round(math.hypot(sx, sy), 3)})
            out.append({"t": round(t, 2), "panel": name, "boxes": boxes})
        if len(out) % 1000 == 0:
            print(f"{t:.0f} s, {len(out)} panel frames", flush=True)
    a.out.write_text(json.dumps({"video": a.video.stem, "weights": a.weights, "conf": CONF, "imgsz": IMGSZ,
                                 "every_s": a.every, "rects": rects, "frames": out}))
    print(f"{len(out)} panel frames -> {a.out}")


if __name__ == "__main__":
    main()
