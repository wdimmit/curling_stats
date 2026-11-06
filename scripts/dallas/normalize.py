"""Rewrite DFW Curling Club's broadcast into one steady three-panel layout.

The DFW stream (1920x1080, 30 fps) cuts between two layouts every half
minute or so, with a 0.3-0.9 s dissolve between them:

* three-panel ("T"): the left long camera (x 0-763), a centre strip of the
  two overheads (x 764-1142; top panel rows 0-525, bottom 526-1079), and the
  right long camera (x 1143-1919). The "First Rock" logo sits top right.
* score view ("S"): one long camera (x 0-1271, rows 0-~950) over a score
  graphic, beside one overhead blown up ~1.58x (x 1274-1919). It shows the
  house in play: the right long camera with the top overhead (S0), or the
  left long camera with the bottom overhead (S1).

Each score-view region is the same camera as its three-panel counterpart,
re-cropped and re-scaled with no rotation, so a fixed similarity maps it
back (fitted by `fit` from median plates: ring fits + ECC for the overheads,
ORB on the static background for the long cameras, 0.9 px RMS). The views a
score frame does not show -- the other house's overhead and long camera,
and the bottom rows of its own long camera under the graphic -- are filled
from the empty median plate rather than frozen, so a stone mid-delivery is
never held as a resting phantom. Dissolve frames repeat the last good
output frame, as a dropped frame would.

The output has the source's frame count and rate, so its times are the
source's times; `analyze` then reads it as any fixed-layout club.

    python normalize.py plates SRC.mp4 OUT.mp4 [--green-to-red OUT2] [--log frames.json]
        [--start FRAME --frames N]   (chunks, joined with ffmpeg's concat demuxer)

``plates`` holds T.png, S0.png, S1.png (median plates) and warps.json.
``--green-to-red`` swaps the green team's handles to red before encoding --
a scaffold so the red/yellow detectors can be tried before a model that knows
green exists, not the fix.
"""

import argparse
import json
import subprocess
import sys

import cv2
import numpy as np

W, H = 1920, 1080
STRIP_X0, STRIP_X1 = 764, 1143
TOP_ROWS, BOTTOM_ROWS = (0, 526), (526, 1080)
RECTS = {  # three-panel regions, (x0, y0, x1, y1)
    "overhead_top": (STRIP_X0, TOP_ROWS[0], STRIP_X1, TOP_ROWS[1]),
    "overhead_bottom": (STRIP_X0, BOTTOM_ROWS[0], STRIP_X1, BOTTOM_ROWS[1]),
    "long_left": (0, 0, STRIP_X0, H),
    "long_right": (STRIP_X1, 0, W, H),
}
S_OVERHEAD = (1274, 0, W, H)
S_LONG = (0, 0, 1272, 950)
# What each score view shows: S0 the top house, S1 the bottom.
SHOWS = {0: ("overhead_top", "long_right"), 1: ("overhead_bottom", "long_left")}

# Static marks of each layout: the score graphic, and the logo top right.
GRAPHIC = (380, 958, 1268, 1075)
LOGO = (1550, 0, 1900, 118)
NEAR, FAR = 14.0, 30.0  # mean abs grey difference: "is this layout" / "is not"
GUARD = 4               # frames either side of a dissolve also repeated


def _crop(img, r):
    x0, y0, x1, y1 = r
    return img[y0:y1, x0:x1]


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


class Plates:
    def __init__(self, d):
        self.T = cv2.imread(f"{d}/T.png")
        self.S = {0: cv2.imread(f"{d}/S0.png"), 1: cv2.imread(f"{d}/S1.png")}
        self.warps = {k: np.array(v, np.float32) for k, v in json.load(open(f"{d}/warps.json")).items()}
        self.graphic = (_crop(_gray(self.S[0]), GRAPHIC) + _crop(_gray(self.S[1]), GRAPHIC)) / 2
        self.logo = _crop(_gray(self.T), LOGO)
        # Which house a score view shows, told by its long camera.
        self.s_long = {c: cv2.resize(_crop(_gray(self.S[c]), (0, 150, 1272, 600)), (160, 56)) for c in (0, 1)}
        # Coverage of each warp inside its target rect: where the score view has no pixels.
        self.covered = {}
        for c, keys in SHOWS.items():
            for key, src in zip(keys, (S_OVERHEAD, S_LONG)):
                m = np.zeros((H, W), np.uint8)
                m[src[1]:src[3], src[0]:src[2]] = 255
                w = cv2.warpAffine(m, self.warps[key], (W, H), flags=cv2.INTER_NEAREST)
                self.covered[key] = w > 0

    def classify(self, frame):
        g = _gray(frame)
        d_s = float(np.abs(_crop(g, GRAPHIC) - self.graphic).mean())
        d_t = float(np.abs(_crop(g, LOGO) - self.logo).mean())
        if d_s < NEAR and d_t > FAR:
            small = cv2.resize(_crop(g, (0, 150, 1272, 600)), (160, 56))
            c = min((0, 1), key=lambda k: float(np.abs(small - self.s_long[k]).mean()))
            return f"S{c}", d_s, d_t
        if d_t < NEAR and d_s > FAR:
            return "T", d_s, d_t
        return "?", d_s, d_t

    def to_three_panel(self, frame, c):
        out = self.T.copy()  # the hidden views stay the empty plate
        for key, src in zip(SHOWS[c], (S_OVERHEAD, S_LONG)):
            x0, y0, x1, y1 = src
            only = np.zeros_like(frame)
            only[y0:y1, x0:x1] = frame[y0:y1, x0:x1]
            w = cv2.warpAffine(only, self.warps[key], (W, H), flags=cv2.INTER_AREA)
            rx0, ry0, rx1, ry1 = RECTS[key]
            cov = self.covered[key][ry0:ry1, rx0:rx1]
            region = out[ry0:ry1, rx0:rx1]
            region[cov] = w[ry0:ry1, rx0:rx1][cov]
        return out


def green_to_red(img):
    """Scaffold: the green team's handles (H ~74, dark) recoloured as red."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    m = (h >= 55) & (h <= 95) & (s >= 60)
    h = h.copy(); h[m] = ((h[m].astype(np.int16) - 74) % 180).astype(np.uint8)
    v = v.copy(); v[m] = np.clip(v[m].astype(np.float32) * 1.6, 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.merge([h, s, v]), cv2.COLOR_HSV2BGR)


def _encoder(path, fps):
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-",
        "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
        "-g", "60", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plates"); ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--green-to-red", metavar="OUT2", help="also write the scaffold video here")
    ap.add_argument("--log", help="per-frame layout log (json)")
    ap.add_argument("--start", type=int, default=0, help="first frame (for chunked runs)")
    ap.add_argument("--frames", type=int, help="this many frames from --start")
    a = ap.parse_args()

    plates = Plates(a.plates)
    cap = cv2.VideoCapture(a.src)
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) - a.start
    if a.frames:
        n = min(n, a.frames)
    cap.set(cv2.CAP_PROP_POS_FRAMES, a.start)

    # Pass 1: classify every frame (cheap), so dissolves can be guarded on both sides.
    kinds, ds, dt = [], [], []
    for i in range(n):
        ok, f = cap.read()
        if not ok:
            n = i
            break
        k, s_, t_ = plates.classify(f)
        kinds.append(k); ds.append(round(s_, 1)); dt.append(round(t_, 1))
        if i % 9000 == 0:
            print(f"classify {i}/{n}", file=sys.stderr, flush=True)
    bad = np.array([k == "?" for k in kinds])
    changes = [i for i in range(1, n) if kinds[i] != kinds[i - 1]]
    for i in changes:
        bad[max(0, i - GUARD):min(n, i + GUARD + 1)] = True
    if a.log:
        json.dump({"fps": fps, "kinds": kinds, "d_s": ds, "d_t": dt}, open(a.log, "w"))
    counts = {k: kinds.count(k) for k in sorted(set(kinds))}
    print(f"{n} frames: {counts}; {int(bad.sum())} repeated", file=sys.stderr, flush=True)

    # Pass 2: write.
    cap.set(cv2.CAP_PROP_POS_FRAMES, a.start)
    enc = _encoder(a.out, fps)
    enc2 = _encoder(a.green_to_red, fps) if a.green_to_red else None
    last = plates.T.copy()
    for i in range(n):
        ok, f = cap.read()
        if not ok:
            f = last
        if not bad[i]:
            k = kinds[i]
            last = f if k == "T" else plates.to_three_panel(f, int(k[1]))
        enc.stdin.write(last.tobytes())
        if enc2:
            enc2.stdin.write(green_to_red(last).tobytes())
        if i % 9000 == 0:
            print(f"write {i}/{n}", file=sys.stderr, flush=True)
    for e in (enc, enc2):
        if e:
            e.stdin.close(); e.wait()


if __name__ == "__main__":
    main()
