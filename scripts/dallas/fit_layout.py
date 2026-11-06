"""Fit a DFW event's layout.json against the canonical plate (normalize_layout.py).

    python fit_layout.py survey RAW.ts [--every 5]          each sample's cuts and dark bar
    python fit_layout.py fit RAW.ts SKELETON.json CANON_T.png OUT_DIR [--every 5]

``survey`` prints, per sampled frame, the x of the strongest vertical cuts and
the mean of the bottom band, to tell the event's views apart by eye.

``fit`` takes a skeleton -- layout.json without the M's, each region also
naming its "kind": "long" or "overhead" -- classifies samples with it, takes a
median plate per view, and fits each region's similarity into the canonical
plate: ORB + RANSAC on the static background for a long camera, the 12-foot
ring's centre and radius (then ECC) for an overhead. It writes OUT_DIR/
layout.json, T.png (the canonical plate), the view plates, and check images
(each view's plate warped into the canonical one, half and half).
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize as N  # noqa: E402


def samples(raw, every):
    cap = cv2.VideoCapture(str(raw))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 10 ** 9
    i, step = 0, int(round(every * fps))
    while i < n:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if not ok:
            break
        yield i / fps, f
        i += step


def cuts(f, k=6):
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h = g.shape[0]
    prof = np.abs(np.diff(g[h // 10:h - h // 10], axis=1)).mean(axis=0)
    xs = []
    for x in np.argsort(prof)[::-1]:
        if all(abs(x - y) > 8 for y in xs):
            xs.append(int(x))
        if len(xs) == k:
            break
    return [(x, round(float(prof[x]), 1)) for x in sorted(xs)], float(g[int(h * 0.9):].mean())


def survey(a):
    for t, f in samples(a.raw, a.every):
        cs, bottom = cuts(f)
        print(f"{t:7.1f}  bottom {bottom:5.1f}  cuts " + " ".join(f"{x}:{v}" for x, v in cs))


def ring(img, rect):
    x0, y0, x1, y1 = rect
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (100, 120, 40), (130, 255, 255))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    (cx, cy), (aw, ah), _ = cv2.fitEllipse(max(cs, key=cv2.contourArea))
    return cx + x0, cy + y0, (aw + ah) / 4


def fit_overhead(src_img, src_rect, canon, canon_rect):
    sx, sy, sr = ring(src_img, src_rect)
    cx, cy, cr = ring(canon, canon_rect)
    s = cr / sr
    M = np.array([[s, 0, cx - s * sx], [0, s, cy - s * sy]], np.float32)
    # ECC on the canonical region, the source warped by M
    x0, y0, x1, y1 = canon_rect
    tgt = cv2.cvtColor(canon, cv2.COLOR_BGR2GRAY)[y0:y1, x0:x1].astype(np.float32)
    srcg = cv2.cvtColor(src_img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    Mc = M.copy(); Mc[0, 2] -= x0; Mc[1, 2] -= y0
    W = cv2.invertAffineTransform(Mc).astype(np.float32)
    try:
        _, W = cv2.findTransformECC(tgt, srcg, W, cv2.MOTION_AFFINE,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
        M2 = cv2.invertAffineTransform(W); M2[0, 2] += x0; M2[1, 2] += y0
        scale = np.hypot(*M2[0, :2]) / s
        if 0.95 < scale < 1.05:
            M = M2.astype(np.float32)
    except cv2.error:
        pass
    return M, {"ring_src": [round(sx, 1), round(sy, 1), round(sr, 1)], "ring_canon": [round(cx, 1), round(cy, 1), round(cr, 1)]}


def fit_long(src_img, src_rect, canon, canon_rect):
    orb = cv2.ORB_create(6000)
    g1, g2 = cv2.cvtColor(src_img, cv2.COLOR_BGR2GRAY), cv2.cvtColor(canon, cv2.COLOR_BGR2GRAY)
    m1 = np.zeros(g1.shape, np.uint8); x0, y0, x1, y1 = src_rect; m1[y0:y1, x0:x1] = 255
    m1[0:int(0.15 * g1.shape[0]), 0:int(0.12 * g1.shape[1])] = 0          # the club logo
    m2 = np.zeros(g2.shape, np.uint8); x0, y0, x1, y1 = canon_rect; m2[y0:y1, x0:x1] = 255
    m2[0:150, 0:220] = 0; m2[0:125, 1540:1920] = 0                          # logos on the canonical plate
    k1, d1 = orb.detectAndCompute(g1, m1); k2, d2 = orb.detectAndCompute(g2, m2)
    ms = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
    p1 = np.float32([k1[m.queryIdx].pt for m in ms]); p2 = np.float32([k2[m.trainIdx].pt for m in ms])
    M, inl = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    r = np.linalg.norm((p1 @ M[:, :2].T + M[:, 2] - p2)[inl.ravel() == 1], axis=1)
    return M.astype(np.float32), {"inliers": int(inl.sum()), "matches": len(ms),
                                  "rms_px": round(float(np.sqrt((r ** 2).mean())), 2),
                                  "scale": round(float(np.hypot(*M[0, :2])), 4),
                                  "rot_deg": round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 3)}


def fit(a):
    from normalize_layout import LayoutPlates

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    skel = json.loads(Path(a.skeleton).read_text())
    canon = cv2.imread(a.canon)
    cv2.imwrite(str(out / "T.png"), canon)
    # classify with the skeleton (identity M's, only the checks matter)
    for v in skel["views"].values():
        for r in v["regions"].values():
            r.setdefault("M", [[1, 0, 0], [0, 1, 0]])
    (out / "layout.json").write_text(json.dumps(skel))
    lp = LayoutPlates(out)
    frames = {}
    for t, f in samples(a.raw, a.every):
        k, _, _ = lp.classify(f)
        if k != "?":
            frames.setdefault(k, []).append(f)
    report = {}
    for name, v in skel["views"].items():
        fs = frames.get(name, [])
        report[name] = {"frames": len(fs)}
        if len(fs) < 3:
            print(f"view {name}: only {len(fs)} frames; not fitted")
            continue
        plate = np.median(np.stack(fs[:200]), axis=0).astype(np.uint8)
        cv2.imwrite(str(out / f"view-{name}.png"), plate)  # never T.png: that is the canonical plate
        check = canon.copy()
        for region, r in v["regions"].items():
            fitter = fit_overhead if r["kind"] == "overhead" else fit_long
            M, info = fitter(plate, r["src"], canon, N.RECTS[region])
            r["M"] = M.tolist()
            report[name][region] = info
            only = np.zeros_like(plate); x0, y0, x1, y1 = r["src"]; only[y0:y1, x0:x1] = plate[y0:y1, x0:x1]
            w = cv2.warpAffine(only, M, (N.W, N.H))
            rx0, ry0, rx1, ry1 = N.RECTS[region]
            check[ry0:ry1, rx0:rx1] = cv2.addWeighted(canon[ry0:ry1, rx0:rx1], 0.5, w[ry0:ry1, rx0:rx1], 0.5, 0)
        cv2.imwrite(str(out / f"check-{name}.jpg"), check)
    (out / "layout.json").write_text(json.dumps(skel, indent=1))
    (out / "fit-report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("survey"); s.add_argument("raw"); s.add_argument("--every", type=float, default=5.0)
    f = sub.add_parser("fit"); f.add_argument("raw"); f.add_argument("skeleton"); f.add_argument("canon")
    f.add_argument("out"); f.add_argument("--every", type=float, default=5.0)
    a = ap.parse_args()
    survey(a) if a.cmd == "survey" else fit(a)


if __name__ == "__main__":
    main()
