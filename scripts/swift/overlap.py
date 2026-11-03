"""The same stone at the same instant in both long cameras, where they overlap
mid-sheet: x and y from each camera's model, and the box width against each
model's lateral scale (a stone is 0.291 m across).

usage: overlap.py timeline.json video [end:shot ...]
"""
import json
import sys

import cv2
import numpy as np

from curling_score.detect import sidemodel
from curling_score.game import hogtime
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView

TEE_Y = C.TEE_TO_TEE_M
tl = json.load(open(sys.argv[1]))
cap = cv2.VideoCapture(sys.argv[2])
model = sidemodel.default_model()


def view_of(name):
    c = tl["calibration"][name]
    return SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"], d_m=c["d_m"],
                    centre_col=c["centre_col"], lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"],
                    centre_line=tuple(c["centre_line"]))


views = {n: view_of(n) for n in ("left", "right")}
want = None if len(sys.argv) < 4 else {tuple(int(v) for v in a.split(":")) for a in sys.argv[3:]}
rows = []
for end in tl["games"][0]["ends"]:
    house = end["house"]
    dest_name = hogtime.CAMERA_FOR[house]
    hog_name = "right" if dest_name == "left" else "left"
    for shot in end["shots"]:
        if want and (end["number"], shot["number"]) not in want:
            continue
        t_hog = shot.get("t_hog_s")
        if t_hog is None or not shot.get("color"):
            continue
        per = {"hog": {}, "dest": {}}
        for role, name in (("hog", hog_name), ("dest", dest_name)):
            v = views[name]
            x0, y0, w, h = v.rect
            frames, times = [], []
            for t in np.arange(t_hog + 1.0, t_hog + 10.0, 0.1):
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, f = cap.read()
                if not ok:
                    continue
                frames.append(f[y0:y0 + h, x0:x0 + w])
                times.append(round(float(t), 2))
            boxes = sidemodel.detect_boxes(model, frames, times, int(v.tee_row) - 80, h, shot["color"],
                                           imgsz=800, conf=0.3)
            for t, bs in zip(times, boxes):
                got = []
                for bx0, by0, bx1, by1, cf in bs:
                    cx, w_px = (bx0 + bx1) / 2, bx1 - bx0
                    yp = v.metres_at(by1) - C.STONE_RADIUS_M
                    row_c = v.row_for(yp)
                    xl = v.lateral_x(cx, row_c)
                    ratio = w_px / (0.291 * v.lateral_px_per_m(by1))
                    if role == "hog":
                        got.append((-xl, TEE_Y - yp, ratio, cf, by1))
                    else:
                        got.append((xl, yp, ratio, cf, by1))
                per[role][t] = got
        both = sorted(set(per["hog"]) & set(per["dest"]))
        for t in both:
            for hx, hy, hr, hc, hrow in per["hog"][t]:
                if not 12.0 < hy < 21.0:
                    continue
                for dx, dy, dr, dc, drow in per["dest"][t]:
                    if abs(dy - hy) < 2.5 and abs(dx - hx) < 0.6:
                        rows.append((end["number"], shot["number"], t, hy, hx, hr, hrow, dy, dx, dr, drow))
        n = [r for r in rows if r[:2] == (end["number"], shot["number"])]
        if n:
            a = np.array([r[3:] for r in n], float)
            print(f"e{end['number']} s{shot['number']:2d} {shot['color']:6s} hog={hog_name:5s} n={len(n):2d} "
                  f"y hog {a[:,0].mean():5.2f} dest {a[:,4].mean():5.2f} | x hog {a[:,1].mean():+.3f} dest {a[:,5].mean():+.3f} "
                  f"diff {np.median(a[:,1]-a[:,5]):+.3f} | width ratio hog {np.median(a[:,2]):.2f} (row {a[:,3].mean():.0f}) "
                  f"dest {np.median(a[:,6]):.2f} (row {a[:,7].mean():.0f})", flush=True)
        else:
            print(f"e{end['number']} s{shot['number']:2d}: no overlap pairs "
                  f"(hog frames with boxes {sum(1 for v in per['hog'].values() if v)}, "
                  f"dest {sum(1 for v in per['dest'].values() if v)})", flush=True)
json.dump(rows, open("overlap_rows.json", "w"))
