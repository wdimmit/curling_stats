"""The centre-ice logo's outline in each long camera's plate, and where each
camera's own model puts it on the ice. Writes pepsi_seg.png to check the
segmentation by eye, and pepsi.json with both outlines (view pixels).

usage: pepsi.py timeline.json   (run where probe/plate.png is)
"""
import json
import sys

import cv2
import numpy as np

from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView

ROWS = {"left": (770, 990), "right": (800, 990)}
tl = json.load(open(sys.argv[1]))
plate = cv2.imread("probe/plate.png")


def view_of(name):
    c = tl["calibration"][name]
    return SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"], d_m=c["d_m"],
                    centre_col=c["centre_col"], lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"],
                    centre_line=tuple(c["centre_line"]))


out, tiles = {}, []
for name in ("left", "right"):
    v = view_of(name)
    x0, _y0, w, _h = v.rect
    r0, r1 = ROWS[name]
    crop = plate[r0:r1, x0:x0 + w]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lum = crop.astype(np.float32).mean(axis=2)
    ice = np.median(lum)
    ink = ((hsv[:, :, 1] > 60) | (lum < ice - 30)).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(ink, np.ones((9, 9), np.uint8)), 8)
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    ink = ink & (lab == k)
    pts = []
    for col in range(w):
        rs = np.nonzero(ink[:, col])[0]
        if len(rs) >= 2:
            pts += [(col, rs[0]), (col, rs[-1])]
    for row in range(ink.shape[0]):
        cs_ = np.nonzero(ink[row])[0]
        if len(cs_) >= 2:
            pts += [(cs_[0], row), (cs_[-1], row)]
    p = np.array(pts, float)
    for _ in range(4):
        (ex, ey), (ea, eb), ang = cv2.fitEllipse(p.astype(np.float32))
        th = np.deg2rad(ang)
        dx, dy = p[:, 0] - ex, p[:, 1] - ey
        u = dx * np.cos(th) + dy * np.sin(th)
        q = -dx * np.sin(th) + dy * np.cos(th)
        rr = np.hypot(u / (ea / 2), q / (eb / 2))
        res = (rr - 1) * min(ea, eb) / 2
        p = p[np.abs(res) < 2.0] if np.abs(res).max() >= 2.0 else p
    print(f"  {name} ellipse inliers {len(p)}, axes {ea:.1f} x {eb:.1f} px, angle {ang:.1f}")
    c = p.copy()
    c[:, 1] += r0
    out[name] = c.tolist()
    ice = np.array([v.to_house(cx, row) for cx, row in c])
    span_x = ice[:, 0].max() - ice[:, 0].min()
    span_y = ice[:, 1].max() - ice[:, 1].min()
    centre_y = (ice[:, 1].max() + ice[:, 1].min()) / 2
    far = C.TEE_TO_TEE_M - centre_y
    print(f"{name}: {len(c)} outline points, rows {c[:, 1].min():.0f}-{c[:, 1].max():.0f}, "
          f"cols {c[:, 0].min():.0f}-{c[:, 0].max():.0f}")
    print(f"  own model: centre x {(ice[:, 0].max() + ice[:, 0].min()) / 2:+.3f} m, centre {centre_y:.2f} m from "
          f"its far tee ({far:.2f} from the other), width {span_x:.2f} m, depth {span_y:.2f} m")
    t = crop.copy()
    for cx, cy in c:
        cv2.circle(t, (int(cx), int(cy - r0)), 1, (0, 255, 0), -1)
    tiles.append(t)
cv2.imwrite("pepsi_seg.png", np.vstack([cv2.resize(t, (730, t.shape[0])) for t in tiles]))
json.dump(out, open("pepsi.json", "w"))
