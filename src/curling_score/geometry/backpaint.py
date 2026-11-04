"""Where an overhead panel's back line sits, found in its paint.

A stone completely beyond the back line is out of play (R2(g)), and nothing
there should keep an end going. 11/03 Tuesday Super League sheet 4: four
minutes into the 7 pm to 9 pm changeover, both houses empty, a tan broom head
held into the corner of the top panel behind the back line read as a yellow
stone for two keyframes. The activity profile ran the last end on to it, the
empty sheet that should have ended the game came out 115 s instead of ~280,
and the night's two games were charted as one of thirteen ends.

The metre test (``y < THROUGH_BACK_Y_M``) cannot see that. ``geometry/
calibrate.py`` fits one ``px_per_m`` from the rings, and these lenses bow the
back line: on 25 recordings of all five sheets the calibration put the line 3
to 6 rows behind its paint on the centre line and more toward the corners, so
the paint itself reads 1.70-1.80 m from the tee, and the strip of ice the
cameras show behind it -- 12 to 38 rows -- reads as on or before the line.

So the line is found where its paint is, in the same calibration median the
rings, hog line and sidelines are read from: the dark band nearest the panel's
back edge, as the paint's OUTER edge -- the side away from the house, which a
stone must be wholly past to be out -- fitted as a quadratic in panel column.
A stone is beyond it when its box's near edge is: the centre plus half the
box, but no more than the centre's distance to the back edge, where the image
clips a box -- a box cut off by the edge is wider than it is tall, and runs
from the edge to twice its centre.

Applied to the activity profile only (``game/profile.count_frame``), after the
detection cache, like the sidelines. Tracking still sees a stone run through:
that is how a delivery that leaves play is told from one that stops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from curling_score.geometry.calibrate import PanelCalib

# The paint is looked for only this close to the panel's back edge. On the
# ten panels probed its outer edge sat 12-38 rows inside it.
BAND_ROWS = 60
# Dark, in BGR: the paint read (29-79, 44-77, 30-79) on those panels, the
# ice about 225. The 12-foot ring's green, which the line touches on the
# centre line, is dark too -- but a column's dark run is read from the back
# edge in, so the green only ever continues a run the paint began.
DARK_MAX = 110            # mean of b, g and r
GREEN_MIN = 30            # g - max(b, r): the ring, not the line
# Columns further than this from a first quadratic are dropped before the
# refit: the centre line, a stone parked on the line, a player's shoe.
OUTLIER_PX = 2.5
# Below these the line is not trusted. The probed panels had the paint in
# 287-302 of their ~298 columns.
MIN_COLUMNS = 150
MAX_SCATTER_PX = 1.5
# Where the paint may read on the centre line, in the calibration's metres.
PLAUSIBLE_Y = (1.55, 2.0)


@dataclass(frozen=True)
class BackLine:
    """One panel's back line: the paint's outer edge, ``row = c0*col**2 + c1*col + c2``.

    ``flipped`` as the panel's calibration: the back edge is row 0 on a top
    panel, the last row on a flipped one. ``height`` is the panel's.
    """

    coef: tuple[float, float, float]
    flipped: bool
    height: int
    columns: int
    scatter_px: float

    def outer_row(self, col: float) -> float:
        return float(np.polyval(self.coef, col))

    def beyond(self, x_px: float, y_px: float, area_px: float) -> bool:
        """Whether a box centred at ``(x_px, y_px)`` of ``area_px`` lies
        wholly past the paint's outer edge, away from the house."""
        half = math.sqrt(max(area_px, 0.0)) / 2.0
        if self.flipped:
            near = y_px - min(half, self.height - 1 - y_px)
            return near > self.outer_row(x_px)
        near = y_px + min(half, y_px)
        return near < self.outer_row(x_px)

    def to_json(self) -> dict:
        return {"outer_edge_row_coef": [round(float(c), 8) for c in self.coef],
                "columns": self.columns,
                "scatter_px": round(float(self.scatter_px), 3)}


def find_back_line(plate, calib: PanelCalib) -> tuple[BackLine | None, str | None]:
    """The back line in one panel's calibration median, a BGR ``(h, w, 3)``
    array, or ``(None, why)``. A panel without one excludes nothing."""
    img = np.asarray(plate, dtype=np.int16)
    h, w = img.shape[:2]
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    dark = (img.mean(axis=2) <= DARK_MAX) & ((g - np.maximum(b, r)) < GREEN_MIN)
    band = min(BAND_ROWS, h)
    cols, outer = [], []
    for c in range(w):
        if calib.flipped:
            rows = np.nonzero(dark[h - band:, c])[0]
            if len(rows):
                cols.append(c)
                outer.append(h - band + rows.max())
        else:
            rows = np.nonzero(dark[:band, c])[0]
            if len(rows):
                cols.append(c)
                outer.append(rows.min())
    if len(cols) < MIN_COLUMNS:
        return None, (f"dark paint in only {len(cols)} of {w} columns within {band} rows "
                      f"of the back edge (need {MIN_COLUMNS})")
    cols = np.asarray(cols, dtype=float)
    outer = np.asarray(outer, dtype=float)
    # The paint is the curve most columns lie on; a robust first fit, from
    # the median of thirds, keeps a parked stone or the frame's own dark edge
    # from pulling it.
    coef = _robust_fit(cols, outer)
    keep = np.abs(outer - np.polyval(coef, cols)) < OUTLIER_PX
    if int(keep.sum()) < MIN_COLUMNS:
        return None, f"only {int(keep.sum())} columns lie on one curve (need {MIN_COLUMNS})"
    coef = np.polyfit(cols[keep], outer[keep], 2)
    keep = np.abs(outer - np.polyval(coef, cols)) < OUTLIER_PX
    coef = np.polyfit(cols[keep], outer[keep], 2)
    scatter = float(np.std(outer[keep] - np.polyval(coef, cols[keep])))
    if scatter > MAX_SCATTER_PX:
        return None, f"the paint's edge scatters {scatter:.2f} px about its fit (limit {MAX_SCATTER_PX})"
    centre_col = calib.center_px[0]
    y = -calib.to_sheet(centre_col, float(np.polyval(coef, centre_col)))[1]
    if not PLAUSIBLE_Y[0] <= y <= PLAUSIBLE_Y[1]:
        return None, f"paint found {y:.3f} m behind the tee on the centre line, outside {PLAUSIBLE_Y}"
    return BackLine(coef=tuple(float(c) for c in coef), flipped=calib.flipped, height=h,
                    columns=int(keep.sum()), scatter_px=scatter), None


def _robust_fit(cols, rows) -> np.ndarray:
    """A first quadratic through the medians of the columns' three thirds,
    each taken about that third's own median column."""
    parts = np.array_split(np.argsort(cols), 3)
    xs = np.array([np.median(cols[p]) for p in parts])
    ys = np.array([np.median(rows[p]) for p in parts])
    return np.polyfit(xs, ys, 2)
