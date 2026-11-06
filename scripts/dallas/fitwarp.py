"""Similarity warps from each score view's regions into the three-panel layout."""
import json
import cv2, numpy as np

T = cv2.imread("plates/T.png"); S = {0: cv2.imread("plates/S0.png"), 1: cv2.imread("plates/S1.png")}
g = lambda im: cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)

def seams(img):
    dx = np.abs(np.diff(g(img).astype(np.float32), axis=1))[100:1000].mean(axis=0)
    dy = np.abs(np.diff(g(img).astype(np.float32), axis=0))[:, 800:1100].mean(axis=1)
    print("col seams", sorted(np.argsort(dx)[-6:].tolist()), "row seams in strip", sorted(np.argsort(dy[300:800])[-4:] + 300))
seams(T)
for c in (0, 1):
    dx = np.abs(np.diff(g(S[c]).astype(np.float32), axis=1))[100:900].mean(axis=0)
    dy = np.abs(np.diff(g(S[c]).astype(np.float32), axis=0))[:, 200:1200].mean(axis=1)
    print("S", c, "col seams", sorted(np.argsort(dx)[-4:].tolist()), "rows", sorted(np.argsort(dy[850:1080])[-4:] + 850))

def ring(img, rect):
    x0, y0, x1, y1 = rect
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (100, 120, 60), (130, 255, 255))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    (cx, cy), (a, b), ang = cv2.fitEllipse(c)
    return cx + x0, cy + y0, a / 2, b / 2

def sim_from(src_pt, src_r, dst_pt, dst_r):
    s = dst_r / src_r
    return np.array([[s, 0, dst_pt[0] - s * src_pt[0]], [0, s, dst_pt[1] - s * src_pt[1]]], np.float32)

def ecc(src, dst_rect, M0, src_rect):
    """Refine M (src full-frame coords -> T full-frame coords) by ECC on the target rect."""
    x0, y0, x1, y1 = dst_rect
    tgt = g(T)[y0:y1, x0:x1].astype(np.float32)
    # express warp in crop coords: dst_crop = M * src_full - (x0, y0)
    M = M0.copy(); M[0, 2] -= x0; M[1, 2] -= y0
    srcf = g(src).astype(np.float32)
    # ECC wants warp mapping template coords -> input coords: invert
    W = cv2.invertAffineTransform(M)
    try:
        _, W = cv2.findTransformECC(tgt, srcf, W.astype(np.float32), cv2.MOTION_AFFINE,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
    except cv2.error as e:
        print("ECC failed", e); return M0
    M = cv2.invertAffineTransform(W); M[0, 2] += x0; M[1, 2] += y0
    return M

out = {}
# overheads: score-view overhead -> top (S0) / bottom (S1) panel
for c, (panel, prect) in {0: ("top", (765, 0, 1143, 526)), 1: ("bottom", (765, 528, 1143, 1080))}.items():
    s_ring = ring(S[c], (1274, 0, 1920, 1080)); t_ring = ring(T, prect)
    print("S", c, "ring", np.round(s_ring, 1), "->", panel, np.round(t_ring, 1))
    M0 = sim_from(s_ring[:2], (s_ring[2] + s_ring[3]) / 2, t_ring[:2], (t_ring[2] + t_ring[3]) / 2)
    M = ecc(S[c], prect, M0, None)
    print("   M0", np.round(M0, 4).tolist(), "\n   M ", np.round(M, 4).tolist())
    out[f"overhead_{panel}"] = M.tolist()

# long cameras: features on the static background
orb = cv2.ORB_create(6000)
for c, (side, trect) in {0: ("right", (1143, 0, 1920, 1080)), 1: ("left", (0, 0, 765, 1080))}.items():
    sm = np.zeros(g(S[c]).shape, np.uint8); sm[0:950, 0:1272] = 255
    sm[0:150, 0:220] = 0
    tm = np.zeros(g(T).shape, np.uint8); x0, y0, x1, y1 = trect; tm[y0:y1, x0:x1] = 255
    tm[0:150, 0:220] = 0; tm[0:125, 1540:1920] = 0  # logos
    k1, d1 = orb.detectAndCompute(g(S[c]), sm); k2, d2 = orb.detectAndCompute(g(T), tm)
    ms = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
    p1 = np.float32([k1[m.queryIdx].pt for m in ms]); p2 = np.float32([k2[m.trainIdx].pt for m in ms])
    M, inl = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    r = np.linalg.norm((p1 @ M[:, :2].T + M[:, 2] - p2)[inl.ravel() == 1], axis=1)
    print("S", c, "long ->", side, "inliers", int(inl.sum()), "/", len(ms), "rms px", round(float(np.sqrt((r ** 2).mean())), 2),
          "scale", round(float(np.hypot(*M[0, :2])), 4), "rot deg", round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 3))
    print("   ORB", np.round(M, 4).tolist())
    out[f"long_{side}"] = M.tolist()
json.dump(out, open("plates/warps.json", "w"), indent=1)

# check renders: warp each score plate into a copy of T
for c, (oh, lg) in {0: ("overhead_top", "long_right"), 1: ("overhead_bottom", "long_left")}.items():
    canvas = T.copy()
    for key, rect, srect in ((oh, (765, 0, 1143, 526) if c == 0 else (765, 528, 1143, 1080), (1274, 0, 1920, 1080)),
                             (lg, (1143, 0, 1920, 1080) if c == 0 else (0, 0, 765, 1080), (0, 0, 1272, 950))):
        src = np.zeros_like(S[c]); x0, y0, x1, y1 = srect; src[y0:y1, x0:x1] = S[c][y0:y1, x0:x1]
        w = cv2.warpAffine(src, np.array(out[key], np.float32), (1920, 1080))
        x0, y0, x1, y1 = rect
        blend = cv2.addWeighted(T[y0:y1, x0:x1], 0.5, w[y0:y1, x0:x1], 0.5, 0)
        canvas[y0:y1, x0:x1] = blend
    cv2.imwrite(f"plates/check{c}.jpg", canvas)
