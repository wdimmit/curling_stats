"""One broadcast layout, as data: which view a frame is, and that view mapped into the canonical one.

Ported from scripts/dallas/normalize_layout.py (where the format is described
and fit_layout.py fits it). A layout is a directory:

    T.png        the canonical empty plate, W x H: what a region no view shows stays
    layout.json  {"size": [w, h], "views": {NAME: {"seams": [x, ...], "no_seams": [x, ...],
                  "dark": [[x0, y0, x1, y1], max_mean] | null,
                  "regions": {CANON_REGION: {"src": [x0, y0, x1, y1], "M": [[a, b, c], [d, e, f]]}}}}}

CANON_REGION is one of `RECTS`; M maps source pixels to canonical ones. A
frame is a view when it is the only view whose checks all pass; otherwise it
is "?" (a dissolve, a graphic, an unknown shot).
"""

import json
import os
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080
_STRIP_X0, _STRIP_X1 = 764, 1143
_TOP_ROWS, _BOTTOM_ROWS = (0, 526), (526, 1080)
# The canonical three-panel regions, (x0, y0, x1, y1): vvy26k01ZcI's layout,
# which the Dallas profile's panel_layout and side_rows describe.
RECTS = {
    "overhead_top": (_STRIP_X0, _TOP_ROWS[0], _STRIP_X1, _TOP_ROWS[1]),
    "overhead_bottom": (_STRIP_X0, _BOTTOM_ROWS[0], _STRIP_X1, _BOTTOM_ROWS[1]),
    "long_left": (0, 0, _STRIP_X0, H),
    "long_right": (_STRIP_X1, 0, W, H),
}
GUARD = 4              # frames either side of a dissolve also repeat the last good frame
SEAM_MIN = 12.0        # mean |dI/dx| across a cut (cuts 15-55, ice < 9)
SEAM_HALF = 3          # px either side of a declared cut
UNKNOWN = "?"

LAYOUTS = Path(__file__).resolve().parent / "layouts"


class Layout:
    def __init__(self, name: str, directory: Path):
        d = Path(directory)
        self.name = name
        self.T = cv2.imread(str(d / "T.png"))
        if self.T is None:
            raise FileNotFoundError(f"layout {name}: no T.png in {d}")
        spec = json.loads((d / "layout.json").read_text())
        self.size = tuple(spec["size"])
        self.views = spec["views"]
        # Each region is warped from its source crop straight into its own
        # canonical rectangle: M composed with both offsets.
        self.warps, self.covered = {}, {}
        for view, v in self.views.items():
            for region, r in v["regions"].items():
                x0, y0, x1, y1 = r["src"]
                rx0, ry0, rx1, ry1 = RECTS[region]
                M = np.vstack([np.array(r["M"], np.float64), [0, 0, 1]])
                into = np.array([[1, 0, -rx0], [0, 1, -ry0], [0, 0, 1]], np.float64)
                outof = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float64)
                Mr = (into @ M @ outof)[:2].astype(np.float32)
                self.warps[(view, region)] = Mr
                m = np.full((y1 - y0, x1 - x0), 255, np.uint8)
                self.covered[(view, region)] = cv2.warpAffine(
                    m, Mr, (rx1 - rx0, ry1 - ry0), flags=cv2.INTER_NEAREST)

    @classmethod
    def load(cls, name_or_dir: str) -> "Layout":
        """A layout by name, under ``BROADCAST_LAYOUT_DIR`` if set, else the
        package's own; or a directory given as a path."""
        p = Path(name_or_dir)
        if p.is_dir() and (p / "layout.json").is_file():
            return cls(p.name, p)
        root = Path(os.environ.get("BROADCAST_LAYOUT_DIR") or LAYOUTS)
        return cls(str(name_or_dir), root / str(name_or_dir))

    def classify(self, frame) -> str:
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        h = g.shape[0]
        prof = np.abs(np.diff(g[h // 10:h - h // 10], axis=1)).mean(axis=0)

        def seam(x):
            return float(prof[max(0, x - SEAM_HALF):x + SEAM_HALF + 1].max())

        hits = []
        for view, v in self.views.items():
            ok = all(seam(x) >= SEAM_MIN for x in v.get("seams", ()))
            ok = ok and not any(seam(x) >= SEAM_MIN for x in v.get("no_seams", ()))
            dark = v.get("dark")
            if ok and dark:
                (x0, y0, x1, y1), most = dark
                ok = float(g[y0:y1, x0:x1].mean()) <= most
            if ok:
                hits.append(view)
        return hits[0] if len(hits) == 1 else UNKNOWN

    def convert(self, frame, kind: str):
        out = self.T.copy()
        for region, r in self.views[kind]["regions"].items():
            x0, y0, x1, y1 = r["src"]
            rx0, ry0, rx1, ry1 = RECTS[region]
            w = cv2.warpAffine(frame[y0:y1, x0:x1], self.warps[(kind, region)],
                               (rx1 - rx0, ry1 - ry0), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
            cv2.copyTo(w, self.covered[(kind, region)], out[ry0:ry1, rx0:rx1])
        return out
