"""Synthetic overhead panels with a painted hog line, for hogpaint and split tests.

Geometry follows the club's real panels: a top panel 534 rows tall with its tee
at row 161, and a flipped bottom panel whose tee sits at row 345. At 80 and 75
px per panel unit, a painted line at rows 516 and 15 sits at y ~= 4.44, where
real panels put theirs.
"""

import numpy as np

from curling_score.geometry import hogpaint
from curling_score.geometry.calibrate import PanelCalib

TOP = PanelCalib(center_px=(150.0, 161.0), px_per_m=80.0, edge_erosion_px=0.0,
                 residual_m=0.0, flipped=False)
BOTTOM = PanelCalib(center_px=(150.0, 345.0), px_per_m=75.0, edge_erosion_px=0.0,
                    residual_m=0.0, flipped=True)

RED_BGR = (60, 60, 200)
BLUE_BGR = (200, 60, 60)


def plate_with_line(h=534, w=300, flipped=False, outer_row=516.0, bow=0.0004,
                    colour=RED_BGR, thick=4, cols=None, jitter=None):
    """A BGR ice plate with a painted line whose OUTER edge is at
    ``outer_row + bow * (col - w/2)**2 (+ jitter(col))``.

    The paint extends from the outer edge toward the house: to smaller rows on
    a top panel, larger rows on a flipped one.
    """
    img = np.full((h, w, 3), 225, np.uint8)
    for c in (range(w) if cols is None else cols):
        o = outer_row + bow * (c - w / 2) ** 2 + (0.0 if jitter is None else jitter(c))
        o = int(round(o))
        rows = range(o, o + thick) if flipped else range(o - thick + 1, o + 1)
        for r in rows:
            if 0 <= r < h:
                img[r, c] = colour
    return img


def line_at(y_tripwire, calib=TOP):
    """A flat HogLine whose tripwire ``y_at(x)`` is ``y_tripwire`` everywhere."""
    y_outer = y_tripwire - hogpaint.LEADING_EDGE_OFFSET_U
    row = calib.to_pixels(0.0, y_outer)[1]
    return hogpaint.HogLine(coef=(0.0, 0.0, float(row)), calib=calib,
                            columns=300, scatter_px=0.0, width_px=4.0)
