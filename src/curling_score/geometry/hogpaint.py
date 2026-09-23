"""Where the hog line's paint sits in an overhead panel, found in the paint.

The panels cannot say where a hog line is in metres -- ``geometry/calibrate.py``
fits one ``px_per_m`` from the house rings, and by the top of the frame the real
scale has fallen to about a third of it -- so a crossing is timed against where
the paint sits in the panel's own coordinates. That used to be one number for
every panel of every video (``split.HOG_APPARENT_Y_M = 4.441``), and hand marks
read in the overhead panels put the paint anywhere from 4.39 to 4.75: top panels
fired 0.17-0.57 s late and bottom panels 0.06-0.17 s early, on all three games
measured (``datasets/hogmarks/receiving-controls.json``).

So the line is found where it is: the red paint near the panel's far edge, in
the same calibration median the rings are fitted on, as the paint's OUTER edge
-- the side a stone arrives from -- fitted as a quadratic in panel column,
because these lenses bow it. See
``docs/superpowers/specs/2026-09-22-phase3-far-tripwire-design.md``.

Not in ``geometry/calibrate.py`` on purpose: the detection cache hashes that
file, and editing it would send every cached video back through the GPU.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from curling_score.geometry.calibrate import PanelCalib

# The paint is looked for only this close to the panel's far edge. Over the
# six panels of three games its outer edge sat 9-88 rows inside it.
BAND_ROWS = 140
# Red, in BGR -- which is what ``ingest/frames`` decodes to. A first probe read
# the frames as RGB and found the line in 8 of 298 columns.
REDNESS_MIN = 25          # r - max(g, b)
RED_MIN = 80
# Columns further than this from a first quadratic are dropped before the
# refit: a parked stone or a player in the median sits well off the curve.
OUTLIER_PX = 2.5
# Below these the line is not trusted. Good panels gave 127-212 columns, a
# 0.52-0.65 px scatter, and an outer edge at 4.32-4.66 on the centre line.
MIN_COLUMNS = 80
MAX_SCATTER_PX = 1.5
PLAUSIBLE_Y = (4.0, 5.0)
# From the paint's outer edge to the tracked centre of a stone whose leading
# edge is on the paint -- the moment a split is timed at. Measured against 51
# hand marks over six panels: panel medians 0.063-0.100, median 0.080.
LEADING_EDGE_OFFSET_U = 0.080


class HogPaintError(RuntimeError):
    """The hog line's paint could not be found well enough to time against."""


@dataclass(frozen=True)
class HogLine:
    """One panel's hog line: the paint's outer edge, ``row = c0*col**2 + c1*col + c2``."""

    coef: tuple[float, float, float]
    calib: PanelCalib
    columns: int
    scatter_px: float

    def outer_edge_y(self, x_m: float) -> float:
        """The paint's outer edge in panel coordinates, at lateral position ``x_m``."""
        col, _row = self.calib.to_pixels(x_m, 0.0)
        return self.calib.to_sheet(col, float(np.polyval(self.coef, col)))[1]

    def y_at(self, x_m: float) -> float:
        """The tripwire at ``x_m``: where a stone's tracked centre is when its
        leading edge first touches the paint."""
        return self.outer_edge_y(x_m) + LEADING_EDGE_OFFSET_U

    def to_json(self) -> dict:
        return {"outer_edge_row_coef": [round(float(c), 8) for c in self.coef],
                "columns": self.columns,
                "scatter_px": round(float(self.scatter_px), 3),
                "offset_u": LEADING_EDGE_OFFSET_U}


def find_hog_line(plate, calib: PanelCalib) -> HogLine:
    """The hog line in one panel's calibration median, a BGR ``(h, w, 3)`` array.

    Raises ``HogPaintError`` with the reason when the paint cannot be trusted.
    There is deliberately no fallback: the old global tripwire was up to half a
    second wrong, and a missing split is better than a wrong one.
    """
    img = np.asarray(plate, dtype=np.float32)
    h, w = img.shape[:2]
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    red = (r - np.maximum(g, b) > REDNESS_MIN) & (r > RED_MIN)
    lo, hi = (0, min(BAND_ROWS, h)) if calib.flipped else (max(0, h - BAND_ROWS), h)
    cols, outer = [], []
    for c in range(w):
        rows = np.nonzero(red[lo:hi, c])[0]
        if len(rows) >= 2:
            cols.append(c)
            # The edge away from the house is the one nearest the frame's far edge.
            outer.append(lo + (rows.min() if calib.flipped else rows.max()))
    if len(cols) < MIN_COLUMNS:
        raise HogPaintError(
            f"red paint in only {len(cols)} of {w} columns within {BAND_ROWS} "
            f"rows of the far edge (need {MIN_COLUMNS})")
    cols = np.asarray(cols, dtype=float)
    outer = np.asarray(outer, dtype=float)
    coef = np.polyfit(cols, outer, 2)
    keep = np.abs(outer - np.polyval(coef, cols)) < OUTLIER_PX
    if int(keep.sum()) < MIN_COLUMNS:
        raise HogPaintError(
            f"only {int(keep.sum())} columns lie on one curve (need {MIN_COLUMNS})")
    coef = np.polyfit(cols[keep], outer[keep], 2)
    scatter = float(np.std(outer[keep] - np.polyval(coef, cols[keep])))
    if scatter > MAX_SCATTER_PX:
        raise HogPaintError(
            f"the paint's edge scatters {scatter:.2f} px about its fit "
            f"(limit {MAX_SCATTER_PX})")
    line = HogLine(coef=tuple(float(c) for c in coef), calib=calib,
                   columns=int(keep.sum()), scatter_px=scatter)
    centre = line.outer_edge_y(0.0)
    if not PLAUSIBLE_Y[0] <= centre <= PLAUSIBLE_Y[1]:
        raise HogPaintError(
            f"paint found at y={centre:.3f} on the centre line, outside {PLAUSIBLE_Y}")
    return line
