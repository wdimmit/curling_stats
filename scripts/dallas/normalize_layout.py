"""Any DFW broadcast layout -> the canonical three-panel layout, from a layout file.

`normalize.py` was written for one event's graphics (vvy26k01ZcI, First Rock):
its views are told apart by that event's logo and score bar, and its
three-panel view IS the canonical layout. DFW's other events frame the same
cameras differently (46wEMds_tYQ, 5U Qualifier: 720p, the overhead strip at
x 535-785 of 1280, no First Rock logo). Here each event is data:

    PLATES/T.png       the canonical empty plate (vvy26k01ZcI's three-panel median),
                       1920x1080: what a region no view shows stays
    PLATES/layout.json {"size": [w, h],               the source frames
                        "views": {NAME: {
                           "seams": [x, ...],        vertical cuts this view has
                           "no_seams": [x, ...],     cuts it must not have
                           "dark": [[x0, y0, x1, y1], max_mean] | null,
                           "regions": {CANON_REGION: {"src": [x0, y0, x1, y1],
                                                       "M": [[a, b, c], [d, e, f]]}}}}}

CANON_REGION is one of `normalize.RECTS`; M maps source pixels to canonical
ones (a similarity, fitted by fit_layout.py). A frame is a view when it is
the only view whose checks all pass; otherwise it is "?" (a dissolve, a
graphic, an unknown shot) and the converter repeats its last good frame.
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize as N  # noqa: E402

SEAM_MIN = 12.0        # mean |dI/dx| across a cut (vvy26k01ZcI: cuts 15-55, ice < 9)
SEAM_HALF = 3          # px either side of a declared cut


class LayoutPlates:
    def __init__(self, d):
        d = Path(d)
        self.T = cv2.imread(str(d / "T.png"))
        layout = json.loads((d / "layout.json").read_text())
        self.size = tuple(layout["size"])
        self.views = layout["views"]
        # Each region is warped from its source crop straight into its own
        # canonical rectangle: M composed with both offsets.
        self.warps, self.covered = {}, {}
        for name, v in self.views.items():
            for region, r in v["regions"].items():
                x0, y0, x1, y1 = r["src"]
                rx0, ry0, rx1, ry1 = N.RECTS[region]
                M = np.vstack([np.array(r["M"], np.float64), [0, 0, 1]])
                into = np.array([[1, 0, -rx0], [0, 1, -ry0], [0, 0, 1]], np.float64)
                outof = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float64)
                Mr = (into @ M @ outof)[:2].astype(np.float32)
                self.warps[(name, region)] = Mr
                m = np.full((y1 - y0, x1 - x0), 255, np.uint8)
                self.covered[(name, region)] = cv2.warpAffine(
                    m, Mr, (rx1 - rx0, ry1 - ry0), flags=cv2.INTER_NEAREST)

    def _profile(self, g):
        h = g.shape[0]
        return np.abs(np.diff(g[h // 10:h - h // 10], axis=1)).mean(axis=0)

    def classify(self, frame):
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        prof = self._profile(g)

        def seam(x):
            return float(prof[max(0, x - SEAM_HALF):x + SEAM_HALF + 1].max())

        hits = []
        for name, v in self.views.items():
            ok = all(seam(x) >= SEAM_MIN for x in v.get("seams", ()))
            ok = ok and not any(seam(x) >= SEAM_MIN for x in v.get("no_seams", ()))
            dark = v.get("dark")
            if ok and dark:
                (x0, y0, x1, y1), most = dark
                ok = float(g[y0:y1, x0:x1].mean()) <= most
            if ok:
                hits.append(name)
        return (hits[0] if len(hits) == 1 else "?"), len(hits), 0.0

    def convert(self, frame, kind):
        out = self.T.copy()
        for region, r in self.views[kind]["regions"].items():
            x0, y0, x1, y1 = r["src"]
            rx0, ry0, rx1, ry1 = N.RECTS[region]
            w = cv2.warpAffine(frame[y0:y1, x0:x1], self.warps[(kind, region)], (rx1 - rx0, ry1 - ry0),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            cv2.copyTo(w, self.covered[(kind, region)], out[ry0:ry1, rx0:rx1])
        return out
