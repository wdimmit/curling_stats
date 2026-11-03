"""Per shot, three frames: the thrower-facing camera at the release the hand
model read, the same camera a second past the hog line with the measured
line drawn, and the target-end camera with the broom and the line extended.

usage: render.py timeline.json video out_dir end:shot ...
"""
import json
import os
import sys

import cv2
import numpy as np

from curling_score.game import hogtime
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView

TEE_Y = C.TEE_TO_TEE_M
HOG_Y = TEE_Y - C.TEE_TO_HOGLINE_M
tl = json.load(open(sys.argv[1]))
cap = cv2.VideoCapture(sys.argv[2])
out_dir = sys.argv[3]
os.makedirs(out_dir, exist_ok=True)


def view_of(name):
    c = tl["calibration"][name]
    return SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"], d_m=c["d_m"],
                    centre_col=c["centre_col"], lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"],
                    centre_line=tuple(c["centre_line"]))


views = {n: view_of(n) for n in ("left", "right")}


def frame(t):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = cap.read()
    return f


def put(img, text, y=28):
    cv2.putText(img, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)


wanted = [tuple(int(v) for v in a.split(":")) for a in sys.argv[4:]]
ends = {e["number"]: e for e in tl["games"][0]["ends"]}
for en, sn in wanted:
    end = ends[en]
    shot = next(s for s in end["shots"] if s["number"] == sn)
    L = shot.get("line") or {}
    if not L.get("at_hog") or not L.get("at_broom"):
        print(en, sn, "no line")
        continue
    dest = views[hogtime.CAMERA_FOR[end["house"]]]
    hog = views["right" if dest is views["left"] else "left"]
    broom = shot["target_broom"]
    xh = L["at_hog"]["x"]
    xb = L["at_broom"]["x"]
    yb = broom["y"]
    slope = (xb - xh) / (yb - HOG_Y)

    def line_x(y):
        return xh + slope * (y - HOG_Y)

    start = L.get("start") or {}
    xs, ys = start.get("x"), start.get("y")

    def aim_x(y):  # the start-to-broom line
        return xs + (broom["x"] - xs) * (ys - y) / (ys - yb)

    def in_hog(x, y):  # timeline -> hog view pixel (full frame)
        c, r = hog.to_image(-x, TEE_Y - y)
        return int(round(hog.rect[0] + c)), int(round(r))

    def in_dest(x, y):
        c, r = dest.to_image(x, y)
        return int(round(dest.rect[0] + c)), int(round(r))

    t_rel, y_rel, x_rel = L["release"] if L.get("release") else (None, None, None)
    tiles = []
    # 1. release
    t_release = shot["t_release_s"] + t_rel if t_rel is not None else shot["t_hog_s"] - 0.5
    f = frame(t_release)
    img = f[:, hog.rect[0]:hog.rect[0] + hog.rect[2]].copy()
    if x_rel is not None:
        c, r = in_hog(x_rel, y_rel)
        cv2.circle(img, (c - hog.rect[0], r), 22, (0, 255, 255), 2)
    for y0, y1, colr in ((TEE_Y, 20.0, (0, 255, 0)),):
        pass
    p_hog = in_hog(-2.375, HOG_Y), in_hog(2.375, HOG_Y)
    cv2.line(img, (p_hog[0][0] - hog.rect[0], p_hog[0][1]), (p_hog[1][0] - hog.rect[0], p_hog[1][1]), (255, 0, 255), 1)
    put(img, f"e{en} s{sn} {shot['color']} {shot.get('shot_type')}: release (hand model)")
    put(img, f"y {y_rel} m, {TEE_Y - y_rel - C.TEE_TO_HOGLINE_M if y_rel else 0:.2f} m before hog", 56)
    tiles.append(img[380:990])
    # 2. past the hog line, line drawn
    f = frame(shot["t_hog_s"] + 1.2)
    img = f[:, hog.rect[0]:hog.rect[0] + hog.rect[2]].copy()
    pts = [in_hog(line_x(y), y) for y in np.arange(TEE_Y - 1, 14.0, -0.25)]
    for a, b in zip(pts, pts[1:]):
        cv2.line(img, (a[0] - hog.rect[0], a[1]), (b[0] - hog.rect[0], b[1]), (0, 255, 0), 2)
    pts = [in_hog(aim_x(y), y) for y in np.arange(ys, 14.0, -0.25)]
    for a, b in zip(pts, pts[1:]):
        cv2.line(img, (a[0] - hog.rect[0], a[1]), (b[0] - hog.rect[0], b[1]), (0, 0, 255), 1)
    for y, x in L.get("hog_path") or []:
        c, r = in_hog(x, y)
        cv2.circle(img, (c - hog.rect[0], r), 3, (255, 255, 0), -1)
    put(img, "green: measured line, red: hack-to-broom")
    tiles.append(img[380:990])
    # 3. target end, broom and the line extended
    f = frame(shot["t_hog_s"] - 1.0)
    img = f[:, dest.rect[0]:dest.rect[0] + dest.rect[2]].copy()
    pts = [in_dest(line_x(y), y) for y in np.arange(19.0, yb - 0.01, -0.25)] + [in_dest(xb, yb)]
    for a, b in zip(pts, pts[1:]):
        cv2.line(img, (a[0] - dest.rect[0], a[1]), (b[0] - dest.rect[0], b[1]), (0, 255, 0), 2)
    pts = [in_dest(aim_x(y), y) for y in np.arange(19.0, yb - 0.01, -0.25)] + [in_dest(broom["x"], yb)]
    for a, b in zip(pts, pts[1:]):
        cv2.line(img, (a[0] - dest.rect[0], a[1]), (b[0] - dest.rect[0], b[1]), (0, 0, 255), 1)
    c, r = in_dest(broom["x"], yb)
    cv2.drawMarker(img, (c - dest.rect[0], r), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
    c, r = in_dest(xb, yb)
    cv2.drawMarker(img, (c - dest.rect[0], r), (0, 255, 0), cv2.MARKER_TILTED_CROSS, 18, 2)
    for y, x in L.get("path") or []:
        c, r = in_dest(x, y)
        cv2.circle(img, (c - dest.rect[0], r), 3, (255, 255, 0), -1)
    put(img, f"broom x {broom['x']:+.2f}  line at broom {xb:+.2f}  miss {L['at_broom']['miss_m']:+.2f} m ({L.get('side')})")
    tiles.append(img[380:990])
    sheet = np.hstack(tiles)
    cv2.imwrite(os.path.join(out_dir, f"e{en}s{sn:02d}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(en, sn, "ok")
