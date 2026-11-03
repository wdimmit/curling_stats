"""Refit the left long camera to its own rings plus the centre-ice logo as the
right camera sees it, then check it against what the fit never saw: stone
widths by row, and the right camera's x for the same stone (overlap).

usage: tie.py timeline.json   (pepsi.json, widths.json, probe/plate.png here)
"""
import json
import sys

import cv2
import numpy as np

from curling_score.geometry import calibrate, endcam
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView

TEE = C.TEE_TO_TEE_M
tl = json.load(open(sys.argv[1]))
plate = cv2.imread("probe/plate.png")
logo = json.load(open("pepsi.json"))
widths = json.load(open("widths.json"))


def view_of(name, **kw):
    c = dict(tl["calibration"][name])
    v = SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"], d_m=c["d_m"],
                 centre_col=c["centre_col"], lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"],
                 centre_line=tuple(c["centre_line"]))
    return SideView(**{**v.__dict__, **kw})


right = view_of("right")
left0 = view_of("left")
# The logo on the ice from the right camera, in the left camera's frame:
# the cameras face each other, so x flips and y counts from the other tee.
R = np.array([right.to_house(c, r) for c, r in logo["right"]])
target = np.c_[-R[:, 0], TEE - R[:, 1]]
L_px = np.array(logo["left"])

x0, y0, w, h = left0.rect
view = np.ascontiguousarray(plate[0:990, x0:x0 + w])
paint = endcam.EndPaint(ring=calibrate.RED_HSV, inner_radius_m=C.R_8FT_M)
outer, inner = endcam.ring_edges(view, paint)
pts = np.vstack([outer, inner])
radii = np.r_[np.full(len(outer), C.R_12FT_M), np.full(len(inner), C.R_8FT_M)]
a, b = left0.centre_line


def model(p, hog_free):
    tee, d, lat = p[0], p[1], p[2]
    hog = p[3] if hog_free else left0.hog_row
    if d <= C.TEE_TO_HOGLINE_M + 0.2 or lat <= 0:
        return None
    v = SideView(rect=left0.rect, tee_row=float(tee), hog_row=float(hog), d_m=float(d),
                 centre_col=a + b * tee, lat_px_per_m_at_tee=float(lat), centre_line=(a, b))
    c, yh = v._map()
    if tee <= yh or L_px[:, 1].min() <= yh or pts[:, 1].min() <= yh:
        return None
    return v


def residuals(p, hog_free, w_logo=1.0):
    v = model(p, hog_free)
    if v is None:
        return np.full(len(pts) + len(L_px), 10.0)
    ring = np.array([np.hypot(*v.to_house(c, r)) for c, r in pts]) - radii
    got = np.array([v.to_house(c, r) for c, r in L_px])
    dist = np.sqrt(((got[:, None, :] - target[None, :, :]) ** 2).sum(-1)).min(axis=1)
    return np.r_[ring, w_logo * dist]


def report(tag, v):
    got = np.array([v.to_house(c, r) for c, r in L_px])
    dist = np.sqrt(((got[:, None, :] - target[None, :, :]) ** 2).sum(-1)).min(axis=1)
    ring = np.abs(np.array([np.hypot(*v.to_house(c, r)) for c, r in pts]) - radii)
    cy = (got[:, 1].max() + got[:, 1].min()) / 2
    print(f"{tag}: tee {v.tee_row:.1f} hog {v.hog_row:.1f} d {v.d_m:.2f} m lat@tee {v.lat_px_per_m_at_tee:.1f} "
          f"yh {v._map()[1]:.1f} | ring med {np.median(ring) * 100:.1f} cm | logo med {np.median(dist) * 100:.1f} cm, "
          f"centre {cy:.2f} m from top tee, width {np.ptp(got[:, 0]):.2f} m (right camera: "
          f"{(target[:, 1].max() + target[:, 1].min()) / 2:.2f}, {np.ptp(target[:, 0]):.2f})")
    hog_y = v.metres_at(left0.hog_row)
    print(f"   the dark row {left0.hog_row:.1f} sits at {hog_y:.2f} m (hog line 6.40)")
    a_ = np.array(widths["left"])
    for lo in range(500, 1000, 100):
        m = (a_[:, 0] >= lo) & (a_[:, 0] < lo + 100)
        if m.sum() >= 5:
            r = a_[m, 0].mean()
            print(f"   rows {lo}-{lo + 100}: stone width / model {np.median(a_[m, 1]) / 0.291 / v.lateral_px_per_m(r):.3f} "
                  f"(n {m.sum()})")


report("before", left0)
best = {}
for hog_free in (False, True):
    p0 = [left0.tee_row, left0.d_m, left0.lat_px_per_m_at_tee] + ([left0.hog_row] if hog_free else [])
    fits = []
    for d0 in (34.0, 40.0, 48.0):
        p0[1] = d0
        p, r = endcam._lm(lambda q: residuals(q, hog_free), np.array(p0, float))
        fits.append((float(np.median(np.abs(r))), p))
    p = min(fits, key=lambda f: f[0])[1]
    v = model(p, hog_free)
    best[hog_free] = v
    report("tied, hog " + ("free" if hog_free else "fixed"), v)
json.dump({k: {"tee_row": v.tee_row, "hog_row": v.hog_row, "d_m": v.d_m, "centre_col": v.centre_col,
               "lat_px_per_m_at_tee": v.lat_px_per_m_at_tee, "centre_line": list(v.centre_line)}
           for k, v in (("hog_fixed", best[False]), ("hog_free", best[True]))}, open("tie.json", "w"), indent=1)
