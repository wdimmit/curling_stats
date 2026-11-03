"""Stone box widths by row in each long camera against its model's lateral
scale: a stone is 0.291 m across, so width / 0.291 is the true px/m on that
row (to the detector's box bias). Fits px/m = k (row - yh) per camera.

usage: widths.py timeline.json video
"""
import json
import sys

import cv2
import numpy as np

from curling_score.detect import sidemodel
from curling_score.geometry.sideview import SideView

tl = json.load(open(sys.argv[1]))
cap = cv2.VideoCapture(sys.argv[2])
model = sidemodel.default_model()


def view_of(name):
    c = tl["calibration"][name]
    return SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"], d_m=c["d_m"],
                    centre_col=c["centre_col"], lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"],
                    centre_line=tuple(c["centre_line"]))


views = {n: view_of(n) for n in ("left", "right")}
got = {"left": [], "right": []}
shots = [s for e in tl["games"][0]["ends"] for s in e["shots"] if s.get("t_hog_s") and s.get("color")]
for s in shots[::2]:
    for name, v in views.items():
        x0, y0, w, h = v.rect
        frames, times = [], []
        for t in np.arange(s["t_hog_s"] - 2.0, s["t_hog_s"] + 9.0, 0.25):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f = cap.read()
            if ok:
                frames.append(f[y0:y0 + h, x0:x0 + w])
                times.append(t)
        for bs in sidemodel.detect_boxes(model, frames, times, int(v.tee_row) - 80, h, s["color"],
                                         imgsz=800, conf=0.5):
            for bx0, by0, bx1, by1, cf in bs:
                if by1 < h - 4 and bx0 > 2 and bx1 < w - 2:
                    got[name].append((by1, bx1 - bx0))
for name, v in views.items():
    a = np.array(got[name])
    print(f"{name}: {len(a)} boxes")
    for lo in range(500, 1000, 50):
        m = (a[:, 0] >= lo) & (a[:, 0] < lo + 50)
        if m.sum() < 5:
            continue
        r = a[m, 0].mean()
        meas = np.median(a[m, 1]) / 0.291
        print(f"  rows {lo}-{lo + 50}: n {m.sum():4d}  stone px/m {meas:6.1f}  model {v.lateral_px_per_m(r):6.1f}  "
              f"ratio {meas / v.lateral_px_per_m(r):.3f}")
    k, b = np.polyfit(a[:, 0], a[:, 1] / 0.291, 1)
    print(f"  fit px/m = {k:.4f} (row - {-b / k:.1f}); model yh {v._map()[1]:.1f}")
json.dump({k: v for k, v in got.items()}, open("widths.json", "w"))
